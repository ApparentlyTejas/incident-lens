#!/usr/bin/env python3
"""Parse raw Thunderbird.log (Loghub / Sandia National Labs) into a Parquet file.

This is a deliberately SEPARATE, standalone pipeline for the "huge dataset" scale
demonstration only - it does not touch bgl_logs/openstack_logs, config.DATASETS, or any of
the investigative tools. The main two-dataset product (BGL + OpenStack) is the hardened,
fully-tested core of this project; Thunderbird exists purely to show SereneDB handling a
real corpus an order of magnitude larger, with real measured query times. Keeping it out of
the shared schema/tool surface means it can never destabilize the part that's actually judged
on investigative quality.

Thunderbird line format (syslog-style, NOT the same shape as BGL despite being a sibling
Loghub HPC dataset - fewer fixed fields, no explicit level column, no repeated-node column):
  <label> <unix_ts> <date> <month> <day> <time> <location> <process>[pid]: <message...>
label '-' = not an alert, same convention as BGL. `location` is one of `node/node`,
`user@node`, or occasionally just `node` - the node id is whichever side doesn't look like a
username. There's no dedicated level field (this is Syslog, not BGL's own RAS/level schema);
`process` (the token before the first `(` or `[`) stands in for BGL's `component`.

Usage: python prep/parse_thunderbird.py <Thunderbird.log> <out.parquet> [--limit N]
"""
import sys, re, os, argparse, datetime as dt
import pyarrow as pa, pyarrow.parquet as pq
sys.path.insert(0, os.path.dirname(__file__))
from templating import template

SCHEMA = pa.schema([
    ("id", pa.int64()), ("ts", pa.timestamp("us")), ("node", pa.string()),
    ("component", pa.string()), ("alert", pa.string()), ("template", pa.string()), ("message", pa.string()),
])

_PROC_RE = re.compile(r"^([^\s(\[:]+)")

def parse(line):
    line = line.rstrip("\r\n")
    toks = line.split(" ")
    if len(toks) < 9:
        return None
    # fields: label unix_ts date month day time location <message...>
    # using the recorded date+time fields directly (not the unix_ts, which is UTC) matches this
    # project's one standing convention: timestamps as recorded in the source file, no timezone
    # conversion (see config.UNTRUSTED_NOTE / corpus_overview's scope_statement) - the two
    # disagree by a fixed offset (Thunderbird's recorded local time vs. UTC), and silently
    # switching to UTC here would make cross-dataset time comparisons quietly wrong.
    label, date_s, time_s, location = toks[0], toks[2], toks[6], toks[7]
    try:
        ts = dt.datetime.strptime(f"{date_s} {time_s}", "%Y.%m.%d %H:%M:%S")
    except ValueError:
        return None
    if "/" in location:
        a, b = location.split("/", 1)
        node = a or b
    elif "@" in location:
        node = location.split("@", 1)[1]
    else:
        node = location
    if not node:
        return None
    rest = line.split(" ", 8)
    if len(rest) != 9:
        return None
    tail = rest[8]
    m = _PROC_RE.match(tail)
    comp = m.group(1) if m else "(unknown)"
    # strip a trailing [pid] / (module) decoration off the component so e.g. crond[2915] and
    # crond[2916] collapse to the same component, not a distinct one per process id
    comp = re.split(r"[\[(]", comp, 1)[0] or "(unknown)"
    colon = tail.find(": ")
    msg = tail[colon + 2:] if colon != -1 else tail
    if not msg:
        msg = tail
    return ts, node, comp, (None if label == "-" else label), template(msg), msg

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("out")
    ap.add_argument("--limit", type=int, default=None, help="stop after this many PARSED rows (for a bounded scale demo)")
    ap.add_argument("--batch", type=int, default=200_000)
    a = ap.parse_args()
    w = pq.ParquetWriter(a.out, SCHEMA)
    cols = {k: [] for k in SCHEMA.names}
    n = bad = 0
    def flush():
        if cols["id"]:
            w.write_table(pa.table(cols, schema=SCHEMA))
            for k in cols: cols[k] = []
    with open(a.src, encoding="utf-8", errors="replace") as f:
        for line in f:
            r = parse(line)
            if r is None:
                bad += 1; continue
            n += 1
            for k, v in zip(SCHEMA.names, (n, *r)):
                cols[k].append(v)
            if n % a.batch == 0: flush()
            if a.limit and n >= a.limit: break
    flush(); w.close()
    print(f"thunderbird: {n} rows parsed, {bad} skipped -> {a.out}")

if __name__ == "__main__":
    main()
