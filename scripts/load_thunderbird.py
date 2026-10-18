#!/usr/bin/env python3
"""Load the (separately parsed) Thunderbird Parquet into its own table, for the "huge dataset"
scale demonstration only - see prep/parse_thunderbird.py's docstring for why this is kept fully
isolated from scripts/load.py and sql/schema.sql. This script never touches bgl_logs,
openstack_logs, or ownership, and never drops anything the main load.py created.

Usage:
  python prep/parse_thunderbird.py data/raw/Thunderbird.log data/processed/thunderbird.parquet [--limit N]
  python scripts/load_thunderbird.py data/processed/thunderbird.parquet
"""
import argparse, os, sys, time, pathlib, psycopg

DSN = os.environ.get("SERENEDB_ADMIN_DSN", "host=127.0.0.1 port=7890 user=postgres dbname=postgres")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("parquet")
    ap.add_argument("--dsn", default=DSN)
    a = ap.parse_args()
    p = pathlib.Path(a.parquet).resolve()
    if not p.exists():
        sys.exit(f"missing {p} - run prep/parse_thunderbird.py first")
    conn = psycopg.connect(a.dsn, autocommit=True)

    conn.execute("DROP TABLE IF EXISTS thunderbird_logs")
    t = time.time()
    conn.execute(f"CREATE TABLE thunderbird_logs AS SELECT * FROM read_parquet('{p}')")
    print(f"table loaded in {time.time()-t:.1f}s")

    # the search dictionary is shared infrastructure sql/indexes.sql also creates for bgl/openstack -
    # create it only if this is the first table to need it, never drop/recreate (that would break
    # the main demo's bgl_idx/os_idx if they're already built against it)
    try:
        conn.execute("CREATE TEXT SEARCH DICTIONARY logdict AS split_text(case := 'lower') WITH (frequency, position, offset)")
    except Exception as e:
        if "already exists" not in str(e): raise

    conn.execute("DROP INDEX IF EXISTS thunderbird_idx")
    t = time.time()
    conn.execute("CREATE INDEX thunderbird_idx ON thunderbird_logs USING inverted (id, ts, node, component, alert, message logdict)")
    conn.execute("VACUUM (REFRESH_TABLE) thunderbird_logs")
    print(f"inverted index built + refreshed in {time.time()-t:.1f}s")

    try:
        conn.execute("GRANT SELECT ON thunderbird_logs TO il_reader")
    except Exception as e:
        print(f"note: could not grant il_reader (run the main scripts/load.py first if you need read access through the MCP tools): {e}")

    n = conn.execute("SELECT count(*) FROM thunderbird_logs").fetchone()[0]
    print(f"thunderbird_logs: {n} rows")

if __name__ == "__main__":
    main()
