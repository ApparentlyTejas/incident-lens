#!/usr/bin/env python3
"""Parse raw BGL.log (Loghub / Oliner & Stearley) into a Parquet file.

BGL line format (space separated, message is the tail):
  <label> <unix_ts> <date> <node> <time> <node_again> <type> <component> <level> <message...>
label '-' = not an alert; anything else is the labelled alert category.
`node_again` normally repeats `node` verbatim, but BGL omits that field entirely on lines with
no specific node (node == '-'), which shifts type/component/level left by one if not accounted
for - real raw lines like "- 1119415930 2005.06.21 - 2005-06-21-21.52.10.214285 RAS KERNEL FATAL
Kill job 20251 timed out." would otherwise come out with component='FATAL', level='Kill'.
A small number of node=='-' lines (well under 0.01% of the file) are themselves garbled in the
raw dataset - the message text is scrambled and RAS/KERNEL/<level> does not sit where expected
even after that fix (e.g. ends up as type='time', component='for', level='a'). We validate that
`level` is a plausible ALL-CAPS token and skip (count as unparsed) the rare line that still isn't,
rather than guess at mangled structure.
A handful of lines also carry literal non-ASCII binary garbage as an extra inserted field (raw
corruption in the original file, not whitespace we can split around) - every real row in the
full corpus has type exactly 'RAS' or the literal string 'NULL', so we validate `type` against
that known pair too and skip the line otherwise.

Usage: python prep/parse_bgl.py <BGL.log> <out.parquet>
"""
import sys, re, datetime as dt
import pyarrow as pa, pyarrow.parquet as pq
sys.path.insert(0, __import__("os").path.dirname(__file__))
from templating import template

SCHEMA = pa.schema([
    ("id", pa.int64()), ("ts", pa.timestamp("us")), ("node", pa.string()),
    ("type", pa.string()), ("component", pa.string()), ("level", pa.string()),
    ("alert", pa.string()), ("template", pa.string()), ("message", pa.string()),
])

LEVEL_RE = re.compile(r"^[A-Z]+$")

def parse(line):
    line = line.rstrip("\r\n")
    toks = line.split(" ")
    if len(toks) < 5:
        return None
    node, tstr = toks[3], toks[4]
    try:
        ts = dt.datetime.strptime(tstr, "%Y-%m-%d-%H.%M.%S.%f")
    except ValueError:
        return None
    has_repeat = len(toks) > 5 and toks[5] == node
    maxsplit = 9 if has_repeat else 8
    p = line.split(" ", maxsplit)
    if len(p) == maxsplit:
        p.append("")   # a real, if rare, shape: level with no message text at all (seen on some FATAL lines)
    if len(p) != maxsplit + 1:
        return None
    label = p[0]
    typ, comp, level, msg = (p[6], p[7], p[8], p[9]) if has_repeat else (p[5], p[6], p[7], p[8])
    if not LEVEL_RE.match(level) or typ not in ("RAS", "NULL"):
        return None   # garbled source line (see module docstring); don't guess at mangled structure
    return ts, node, typ, comp, level, (None if label == "-" else label), template(msg), msg

def main(src, out, batch=200_000):
    w = pq.ParquetWriter(out, SCHEMA)
    cols = {k: [] for k in SCHEMA.names}
    n = bad = 0
    def flush():
        if cols["id"]:
            w.write_table(pa.table(cols, schema=SCHEMA))
            for k in cols: cols[k] = []
    with open(src, encoding="utf-8", errors="replace") as f:
        for line in f:
            r = parse(line)
            if r is None:
                bad += 1; continue
            n += 1
            for k, v in zip(SCHEMA.names, (n, *r)):
                cols[k].append(v)
            if n % batch == 0: flush()
    flush(); w.close()
    print(f"bgl: {n} rows parsed, {bad} skipped -> {out}")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
