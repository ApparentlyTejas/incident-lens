#!/usr/bin/env python3
"""Latency/scale benchmark over the FULL loaded corpus (run after scripts/load.py, no --sample).
Each tool is called N times (cold call excluded from stats) and min/median/p95/max are reported,
alongside the exact row counts the underlying queries touched - evidence for "how much data was
actually queried, whether results stay useful" rather than a synthetic microbenchmark.

Usage: python eval/benchmark.py [--n 20]
"""
import argparse, json, statistics as st, sys, pathlib, time
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server import tools as T, db

CALLS = [
    ("corpus_overview (whole corpus, both datasets)", lambda: T.corpus_overview()),
    ("search_logs - common term, full period", lambda: T.search_logs("bgl", "invalid or missing program image", mode="phrase", limit=20)),
    ("search_logs - fuzzy mode", lambda: T.search_logs("bgl", "paritty", mode="fuzzy", limit=20)),
    ("nearby_events - any_node, +-60s", lambda: T.nearby_events("bgl", 4988, before_seconds=60, after_seconds=60, scope="any_node", limit=40)),
    ("count_by - node (1024 groups)", lambda: T.count_by("bgl", "node", query="invalid or missing program image", mode="phrase")),
    ("count_by - team, whole corpus, severe only", lambda: T.count_by("bgl", "team", severity="severe")),
    ("trend - week buckets, full 7-month span", lambda: T.trend("bgl", bucket="week")),
    ("episodes - whole corpus, severe only", lambda: T.episodes("bgl", severity="severe", top=10)),
    ("recurring_patterns - whole corpus, severe only", lambda: T.recurring_patterns("bgl", severity="severe", limit=15)),
    ("latency_summary - whole openstack corpus", lambda: T.latency_summary()),
]

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=15); a = ap.parse_args()
    rows_total = db.scalar("select count(*) from bgl_logs") + db.scalar("select count(*) from openstack_logs")
    print(f"Corpus: {rows_total:,} total rows across bgl_logs + openstack_logs\n")
    results = []
    for label, fn in CALLS:
        fn()  # cold call: warms any connection/plan caching, excluded from timing
        times = []
        last = None
        for _ in range(a.n):
            t0 = time.perf_counter(); last = fn(); times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        p95 = times[int(0.95 * (len(times) - 1))]
        if "datasets" in last: scanned = sum(d.get("rows", 0) for d in last["datasets"].values())
        else: scanned = last.get("total_matches") or last.get("groups_total") or last.get("entries")
        results.append((label, st.median(times), min(times), max(times), p95, scanned, last["evidence_id"]))
        print(f"{label:52s}  median {st.median(times):7.1f} ms  min {min(times):7.1f}  max {max(times):7.1f}  p95 {p95:7.1f}  matches={scanned}  [{last['evidence_id']}]")
    print(f"\n{a.n} calls per tool, cold call excluded. Full table written to eval/benchmark_results.md")
    lines = [f"# Benchmark: {rows_total:,} rows (bgl_logs + openstack_logs), SereneDB, {a.n} calls per tool\n",
             "| tool call | median ms | min ms | max ms | p95 ms | matches/rows touched | evidence |",
             "|---|---|---|---|---|---|---|"]
    for label, med, mn, mx, p95, scanned, eid in results:
        lines.append(f"| {label} | {med:.1f} | {mn:.1f} | {mx:.1f} | {p95:.1f} | {scanned} | `{eid}` |")
    (ROOT / "eval/benchmark_results.md").write_text("\n".join(lines) + "\n")

if __name__ == "__main__":
    main()
