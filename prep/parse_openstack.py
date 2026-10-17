#!/usr/bin/env python3
"""Parse raw OpenStack logs (Loghub) into one Parquet file.

Line format:
  <logfile> <date> <time> <pid> <LEVEL> <logger> [req-<id> <user> <tenant> - - -] <message>
or, without a request context:
  <logfile> <date> <time> <pid> <LEVEL> <logger> [-] <message>   (or plain message)
Service = prefix of the logfile (nova-api / nova-compute / nova-scheduler).
For nova-api wsgi lines we also extract http method, url, status, len, latency ('time: x').
Anomaly label: Loghub's anomaly_labels.txt lists VM INSTANCE ids, not request ids - instance ids
never appear as a line's request_id, so joining on request_id (as an earlier version of this
script did) silently matches nothing. The actual ground truth is which raw file a line came from:
every line in a file whose name contains "abnormal" is anomalous, every line from a file whose
name contains "normal" is not. --labels is only used to report how many instances were flagged.

Usage: python prep/parse_openstack.py <out.parquet> <log> [<log> ...] [--labels anomaly_labels.txt]
"""
import sys, re, datetime as dt
import pyarrow as pa, pyarrow.parquet as pq
sys.path.insert(0, __import__("os").path.dirname(__file__))
from templating import template

SCHEMA = pa.schema([
    ("id", pa.int64()), ("ts", pa.timestamp("us")), ("service", pa.string()),
    ("pid", pa.int32()), ("level", pa.string()), ("component", pa.string()),
    ("request_id", pa.string()), ("tenant_id", pa.string()),
    ("http_method", pa.string()), ("http_url", pa.string()), ("http_status", pa.int32()),
    ("latency_s", pa.float64()), ("anomalous", pa.bool_()), ("template", pa.string()), ("message", pa.string()),
])
HEAD = re.compile(r"^(\S+) (\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d\.\d+) (\d+) (\w+) (\S+) (.*)$")
CTX = re.compile(r"^\[req-([0-9a-f-]+) (\S+) (\S+) [^\]]*\] (.*)$")
WSGI = re.compile(r'"(\w+) (\S+) HTTP/[\d.]+" status: (\d+) len: \d+ time: ([\d.]+)')

def parse(line):
    m = HEAD.match(line.rstrip("\r\n"))
    if not m: return None
    logfile, d, t, pid, level, comp, rest = m.groups()
    ts = dt.datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M:%S.%f")
    service = logfile.split(".log")[0]
    req = tenant = None
    c = CTX.match(rest)
    if c: req, _user, tenant, rest = c.groups()
    meth = url = status = lat = None
    w = WSGI.search(rest)
    if w: meth, url, status, lat = w.group(1), w.group(2), int(w.group(3)), float(w.group(4))
    return ts, service, int(pid), level, comp, req, tenant, meth, url, status, lat, template(rest), rest

def main(out, files, labels=None):
    if labels:
        uuid = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
        instances = {m.group(0) for ln in open(labels, errors="replace") for m in [uuid.search(ln)] if m}
        print(f"labels: {len(instances)} anomalous VM instance id(s) named in {labels} (for reference only; "
              "the anomalous column below is set from which raw file each line came from)")
    cols = {k: [] for k in SCHEMA.names}
    n = bad = 0
    for fp in files:
        anomalous = "abnormal" in fp.lower() if any("abnormal" in f.lower() or "normal" in f.lower() for f in files) else None
        for line in open(fp, encoding="utf-8", errors="replace"):
            r = parse(line)
            if r is None: bad += 1; continue
            n += 1
            row = (n, *r[:-2], anomalous, r[-2], r[-1])
            for k, v in zip(SCHEMA.names, row): cols[k].append(v)
    pq.write_table(pa.table(cols, schema=SCHEMA), out)
    print(f"openstack: {n} rows parsed, {bad} skipped -> {out}")

if __name__ == "__main__":
    a = sys.argv[1:]; labels = None
    if "--labels" in a:
        i = a.index("--labels"); labels = a[i+1]; del a[i:i+2]
    main(a[0], a[1:], labels)
