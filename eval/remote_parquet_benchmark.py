#!/usr/bin/env python3
"""Remote-Parquet scale demonstration for the "Working with Huge Datasets" award - a second,
even larger angle than the local Thunderbird load in docs/HUGE_DATASET.md.

The challenge brief lists 50 billion rows of OpenTelemetry demo-app logs sitting in public S3
as Parquet (50 files of ~1B rows each). The point isn't to load this - it's to point SereneDB
at the URL and query it where it lives, no download, no local storage, no loading job. This
script does exactly that: every number below is a real query against a real remote file.

Usage: python eval/remote_parquet_benchmark.py
Needs SERENEDB_ADMIN_DSN (or defaults to host=127.0.0.1 port=7890 user=postgres dbname=postgres) -
plain SQL, no MCP tool wraps this on purpose (see the module-level note in prep/parse_thunderbird.py
for the same isolation rationale: this must never be able to affect the main BGL/OpenStack product).
"""
import os, time, psycopg

DSN = os.environ.get("SERENEDB_ADMIN_DSN", "host=127.0.0.1 port=7890 user=postgres dbname=postgres")
URL = "https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet"

QUERIES = [
    ("exact row count, whole file (1 billion rows)",
     f"SELECT count(*) FROM read_parquet('{URL}')"),
    ("group by service + severity (full scan, real text columns)",
     f"SELECT ServiceName, SeverityText, count(*) AS n FROM read_parquet('{URL}') "
     f"GROUP BY 1, 2 ORDER BY n DESC LIMIT 10"),
    ("filtered search, first 3 error rows (early-exit via LIMIT)",
     f"SELECT Timestamp, ServiceName, Body FROM read_parquet('{URL}') WHERE SeverityText = 'error' LIMIT 3"),
    ("exact count of high-severity rows (SeverityNumber >= 17, full scan)",
     f"SELECT count(*) FROM read_parquet('{URL}') WHERE SeverityNumber >= 17"),
]

def main():
    conn = psycopg.connect(DSN, autocommit=True)
    print(f"Remote file: {URL}\nNo local download, no local storage - SereneDB reads it directly off S3.\n")
    for label, sql in QUERIES:
        t0 = time.time()
        rows = conn.execute(sql).fetchall()
        ms = (time.time() - t0) * 1000
        print(f"{label}\n  {ms:,.0f} ms  ->  {rows}\n")

if __name__ == "__main__":
    main()
