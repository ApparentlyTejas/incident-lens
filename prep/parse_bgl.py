#!/usr/bin/env python3
"""Parse raw BGL.log (Loghub / Oliner & Stearley) into a Parquet file.

BGL line format (space separated, message is the tail):
  <label> <unix_ts> <date> <node> <time> <node_again> <type> <component> <level> <message...>
label '-' = not an alert; anything else is the labelled alert category.

Usage: python prep/parse_bgl.py <BGL.log> <out.parquet>
"""
import sys, datetime as dt
import pyarrow as pa, pyarrow.parquet as pq
sys.path.insert(0, __import__("os").path.dirname(__file__))
from templating import template

SCHEMA = pa.schema([
    ("id", pa.int64()), ("ts", pa.timestamp("us")), ("node", pa.string()),
    ("type", pa.string()), ("component", pa.string()), ("level", pa.string()),
    ("alert", pa.string()), ("template", pa.string()), ("message", pa.string()),
])

def parse(line):
    p = line.rstrip("\r\n").split(" ", 9)
    if len(p) < 10:
        return None
    label, _unix, _date, node, tstr, _node2, typ, comp, level, msg = p
    try:
        ts = dt.datetime.strptime(tstr, "%Y-%m-%d-%H.%M.%S.%f")
    except ValueError:
        return None
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
