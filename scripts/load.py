#!/usr/bin/env python3
"""Load Incident Lens data into SereneDB.

  python scripts/load.py --sample         # 2k-line samples (fast; dev + tests)
  python scripts/load.py                  # full corpus from data/processed/*.parquet

Runs sql/schema.sql, sql/ownership.sql, sql/indexes.sql, sql/readonly_role.sql and prints
row counts and timings (index build time is reported for the README).
"""
import argparse, os, sys, time, pathlib, psycopg

ROOT = pathlib.Path(__file__).resolve().parent.parent
DSN = os.environ.get("SERENEDB_ADMIN_DSN", "host=127.0.0.1 port=7890 user=postgres dbname=postgres")

def run_file(conn, path, subs):
    sql = (ROOT / "sql" / path).read_text()
    for k, v in subs.items():
        sql = sql.replace("{{%s}}" % k, v)
    for stmt in [s.strip() for s in sql.split(";") if s.strip() and not all(l.strip().startswith("--") or not l.strip() for l in s.splitlines())]:
        try:
            conn.execute(stmt)
        except Exception as e:
            if "readonly_role" in path and "already exists" in str(e): continue
            raise SystemExit(f"FAILED in {path}:\n{stmt}\n-> {e}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--dsn", default=DSN)
    a = ap.parse_args()
    if a.sample:
        bgl, osp = ROOT/"data/sample/bgl_2k.parquet", ROOT/"data/sample/openstack_2k.parquet"
    else:
        bgl, osp = ROOT/"data/processed/bgl.parquet", ROOT/"data/processed/openstack.parquet"
    for p in (bgl, osp):
        if not p.exists(): sys.exit(f"missing {p} - run scripts/setup.sh (full) or prep on the samples first")
    conn = psycopg.connect(a.dsn, autocommit=True)
    subs = {"BGL_PARQUET": str(bgl), "OS_PARQUET": str(osp)}
    t = time.time(); run_file(conn, "schema.sql", subs); print(f"tables loaded in {time.time()-t:.1f}s")
    run_file(conn, "ownership.sql", subs)
    t = time.time(); run_file(conn, "indexes.sql", subs); print(f"inverted indexes built + refreshed in {time.time()-t:.1f}s")
    if a.sample:
        # same "is this chunk pure comments/blank lines" check as run_file() above - the previous
        # version's `... or "INSERT" in x or "VACUUM" in x` was meant to rescue a statement with a
        # leading comment line, but by operator precedence it also force-included (and then tried
        # to execute as an EMPTY statement) any pure-comment chunk that merely mentioned those words
        for s in (ROOT / "data/sample/fixtures.sql").read_text().split(";"):
            s = s.strip()
            if not s or all(l.strip().startswith("--") or not l.strip() for l in s.splitlines()): continue
            conn.execute("\n".join(l for l in s.splitlines() if not l.strip().startswith("--")))
    run_file(conn, "readonly_role.sql", subs)
    for t_ in ("bgl_logs", "openstack_logs", "ownership"):
        print(f"{t_}: {int(conn.execute(f'select count(*) from {t_}').fetchone()[0])} rows")
    unmapped = conn.execute("""select 'bgl', component from bgl_logs where component not in (select component from ownership where dataset='bgl') group by 2
        union all select 'openstack', service from openstack_logs where service not in (select component from ownership where dataset='openstack') group by 2""").fetchall()
    if unmapped: print("WARNING components without an owner (add to sql/ownership.sql):", unmapped)

if __name__ == "__main__":
    main()
