#!/usr/bin/env python3
"""Real query-time benchmark against thunderbird_logs - the "huge dataset" scale demonstration.
Run after prep/parse_thunderbird.py + scripts/load_thunderbird.py. Every number here is a real
query actually executed against the real loaded table, same min/median/p95/max methodology as
eval/benchmark.py uses for the main BGL/OpenStack corpus - this is a separate script, not a
retrofit of that one, because thunderbird_logs is a deliberately standalone table (see
prep/parse_thunderbird.py's docstring for why it's kept out of the main tool surface).

Usage: python eval/huge_dataset_benchmark.py [--n 10]
"""
import argparse, os, statistics as st, sys, time
import psycopg

DSN = os.environ.get("SERENEDB_DSN", "host=127.0.0.1 port=7890 user=il_reader password=il_reader dbname=postgres")

QUERIES = [
    ("exact row count (whole table)", "SELECT count(*) FROM thunderbird_logs", []),
    ("full-text search, common term", "SELECT count(*) FROM thunderbird_idx WHERE message @@ ts_phrase(%s)", ["connection timed out"]),
    ("full-text search + time range", "SELECT count(*) FROM thunderbird_idx WHERE message @@ ts_phrase(%s) AND ts >= %s AND ts < %s",
     ["kernel panic", "2005-01-01", "2006-01-01"]),
    ("group by node (top 20)", "SELECT node, count(*) AS n FROM thunderbird_logs GROUP BY 1 ORDER BY 2 DESC LIMIT 20", []),
    ("group by component (top 20)", "SELECT component, count(*) AS n FROM thunderbird_logs GROUP BY 1 ORDER BY 2 DESC LIMIT 20", []),
    ("distinct nodes", "SELECT count(DISTINCT node) FROM thunderbird_logs", []),
    ("labelled-alert rows", "SELECT count(*) FROM thunderbird_logs WHERE alert IS NOT NULL", []),
    ("week-bucketed trend", "SELECT date_trunc('week', ts) AS wk, count(*) FROM thunderbird_logs GROUP BY 1 ORDER BY 1", []),
]

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=10); a = ap.parse_args()
    conn = psycopg.connect(DSN, autocommit=True)
    try:
        total = conn.execute("SELECT count(*) FROM thunderbird_logs").fetchone()[0]
    except Exception as e:
        sys.exit(f"thunderbird_logs not loaded - run prep/parse_thunderbird.py + scripts/load_thunderbird.py first ({e})")
    print(f"thunderbird_logs: {total:,} rows\n")
    print(f"{'query':<42} {'min':>8} {'median':>8} {'p95':>8} {'max':>8}  (ms, n={a.n})")
    for label, sql, params in QUERIES:
        conn.execute(sql, params or None)  # cold call excluded from timing, same as eval/benchmark.py
        times = []
        for _ in range(a.n):
            t0 = time.perf_counter()
            conn.execute(sql, params or None).fetchall()
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        p95 = times[int(0.95 * (len(times) - 1))]
        print(f"{label:<42} {times[0]:>7.1f}  {st.median(times):>7.1f}  {p95:>7.1f}  {times[-1]:>7.1f}")

if __name__ == "__main__":
    main()
