#!/usr/bin/env python3
"""Parse raw Zookeeper.log (Loghub) into a Parquet file.

Zookeeper line format (Java-style, consistent, no observed exceptions in the real file):
  <date> <time>,<ms> - <LEVEL>  [<thread>:<class>@<line>] - <message>
e.g. "2015-07-29 17:41:41,536 - INFO  [main:QuorumPeerConfig@101] - Reading configuration from: ..."

No per-node field - this is one ensemble member's own log, not a cluster-wide view. `thread`
(e.g. "main", "SyncThread:0", "QuorumPeer[myid=1]/0:0:0:0:0:0:0:0:2181:QuorumCnxManager$Listener@...")
is this dataset's closest analog to BGL's `node` - the thing that groups a burst of related
activity - so it's used as the partition column; `class` (e.g. QuorumPeerConfig,
NIOServerCnxnFactory) is the component/ownership column, same role as BGL's `component`.

Usage: python prep/parse_zookeeper.py <Zookeeper.log> <out.parquet>
"""
import sys, re, os, datetime as dt
import pyarrow as pa, pyarrow.parquet as pq
sys.path.insert(0, os.path.dirname(__file__))
from templating import template

SCHEMA = pa.schema([
    ("id", pa.int64()), ("ts", pa.timestamp("us")), ("thread", pa.string()),
    ("component", pa.string()), ("level", pa.string()), ("template", pa.string()), ("message", pa.string()),
])

LINE_RE = re.compile(
    # the bracket content itself can contain a nested ']' (e.g. thread name
    # "QuorumPeer[myid=1]/0:0:0:0:0:0:0:0:2181:QuorumPeer"), so this has to match
    # greedily to the LAST '] - ' on the line, not stop at the first ']'
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - ([A-Z]+)\s+\[(.+)\] - (.*)$"
)

def parse(line):
    line = line.rstrip("\r\n")
    m = LINE_RE.match(line)
    if not m:
        return None
    ts_raw, level, bracket, msg = m.groups()
    try:
        ts = dt.datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S,%f")
    except ValueError:
        return None
    # bracket is "thread:Class@line" - the class name is everything between the LAST ':' before
    # '@' and the '@' itself, since thread names themselves can contain ':' (e.g. "SyncThread:0",
    # "QuorumPeer[myid=1]/0:0:0:0:0:0:0:0:2181:QuorumCnxManager$Listener")
    at = bracket.rfind("@")
    if at == -1:
        thread, comp = bracket, "(unknown)"
    else:
        head = bracket[:at]
        colon = head.rfind(":")
        if colon == -1:
            thread, comp = "(unknown)", head
        else:
            thread, comp = head[:colon], head[colon + 1:]
    if not msg:
        return None
    return ts, thread, comp, level, template(msg), msg

def main():
    if len(sys.argv) != 3:
        sys.exit("Usage: python prep/parse_zookeeper.py <Zookeeper.log> <out.parquet>")
    src, out = sys.argv[1], sys.argv[2]
    w = pq.ParquetWriter(out, SCHEMA)
    cols = {k: [] for k in SCHEMA.names}
    n = bad = 0
    batch = 50_000
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
            for k, v in zip(SCHEMA.names, (n, *r), strict=False):
                cols[k].append(v)
            if n % batch == 0: flush()
    flush(); w.close()
    print(f"zookeeper: {n} rows parsed, {bad} skipped -> {out}")

if __name__ == "__main__":
    main()
