# Benchmark: 4,955,757 rows (bgl_logs + openstack_logs), SereneDB, 15 calls per tool

| tool call | median ms | min ms | max ms | p95 ms | matches/rows touched | evidence |
|---|---|---|---|---|---|---|
| corpus_overview (whole corpus, both datasets) | 108.6 | 98.6 | 146.8 | 135.6 | 4955757 | `E-0033` |
| search_logs - common term, full period | 125.7 | 118.9 | 195.1 | 153.7 | 112584 | `E-0049` |
| search_logs - fuzzy mode | 87.6 | 81.5 | 108.7 | 99.7 | 121498 | `E-0065` |
| nearby_events - any_node, +-60s | 8.0 | 6.9 | 10.0 | 9.4 | 32 | `E-0081` |
| count_by - node (1024 groups) | 43.6 | 39.0 | 51.1 | 49.1 | 112584 | `E-0097` |
| count_by - team, whole corpus, severe only | 37.3 | 35.5 | 40.8 | 38.5 | 988783 | `E-0113` |
| trend - week buckets, full 7-month span | 12.5 | 11.4 | 13.3 | 13.3 | 4747945 | `E-0129` |
| episodes - whole corpus, severe only | 434.8 | 398.1 | 518.0 | 469.4 | 988783 | `E-0145` |
| recurring_patterns - whole corpus, severe only | 130.1 | 107.8 | 194.7 | 179.7 | 988783 | `E-0161` |
| latency_summary - whole openstack corpus | 15.8 | 13.5 | 32.5 | 22.5 | 207812 | `E-0177` |

## Resource usage during this run

`docker stats` on the SereneDB container, sampled mid-run: under 600MB resident memory (well under the 3.8GB container limit), under 1% CPU on this snapshot (CPU is bursty - near 0% between calls, briefly higher during each query). No tuning or warm-up beyond one excluded cold call per tool; every number above is the database actually scanning the full 4.7M/207K-row tables, not a cached or pre-aggregated result.

## `search_logs` relevance mode: found and fixed a real planner interaction, not just documented

An earlier version of this table had `search_logs` in its default relevance-ranked mode at ~1.5-2.3s - roughly 9x slower than the identical query in `order=time` mode. Root-caused with `EXPLAIN`: SereneDB's inverted-index engine (iresearch-style) has a fast top-k path for `ORDER BY BM25(...)` that uses block-max score pruning to avoid scoring every match - but that fast path is lost entirely whenever the index scan's filter also includes a range predicate (`Granular Range` in the plan), even one that excludes nothing. Direct timing proved it wasn't a selectivity issue: the exact same 112,584-row phrase match ran in ~36ms with no `ts` filter at all, and in 900ms-1.5s with a `ts >= .. AND ts < ..` bound covering the *entire dataset span* (a provable no-op). Narrower, genuinely restrictive ranges scaled roughly linearly with match count once the range filter was present (3,384 matches -> 7.6ms, 33,112 -> 158ms, 93,968 -> 404ms), confirming the range filter disables pruning rather than the planner misjudging row counts.

The fix (`server/common.py`'s `where(..., include_ts=...)`, used from `server/tools.py::search_logs`): when the resolved period is provably the dataset's full span - the common case, since every tool defaults to "whole corpus, no time filter" - drop the redundant `ts` bound from the scored query entirely, restoring the fast pruning path. An explicit, genuinely narrower `from_ts`/`to_ts` still gets the correctly-filtered (and correctly slower, since those rows must be scored) query - confirmed by both a direct re-check (3,384 matches, 7.6ms) and a locked-in regression test (`tests/test_tools.py::test_search_logs_drops_redundant_ts_bound_but_keeps_narrow_one_correct`). Measured result: the full-period relevance search above now runs at a 125.7ms median, in the same range as every other tool, down from the original ~1.5-2.3s.

## Evidence ledger is now tamper-evident, not just append-only

Added hash-chaining (`server/ledger.py`): every stored entry's hash is `sha256(previous entry's hash + this entry's own content)`, so editing or deleting any past row - even by hand, bypassing every tool - breaks the chain from that point on. A new `verify_ledger_integrity` tool recomputes every hash from the raw stored rows (trusting nothing cached) and reports the first broken entry, if any. Verified directly: a clean ledger reports `ok: true`; hand-editing one stored row's `total_matches` with a raw SQLite `UPDATE` (bypassing the server entirely) makes `verify_ledger_integrity` report `ok: false` with that exact entry named - locked in as `tests/test_tools.py::test_ledger_chain_verifies_clean_and_catches_direct_tampering` (run against its own isolated ledger file, so the test's deliberate tampering never corrupts the real one). This turns "every number traces to a query that actually ran" from an assertion about the code into something a skeptic can check without trusting the code at all.

## Connection resilience: survives a SereneDB restart mid-session

While benchmarking, the SereneDB connection was interrupted twice during testing (once after a hand-written diagnostic query, once under concurrent test load). `server/db.py` previously cached one connection and only checked its local `.closed` flag, which stays `False` even after the remote server has restarted - the next query would surface a raw `psycopg.OperationalError` instead of recovering. Fixed: `run()` now retries once with a freshly opened connection on `OperationalError`. Verified directly: a long-lived Python process queried the database, had SereneDB restarted out from under it (`docker restart serenedb`), and its next query on the same cached-connection object succeeded with no process restart needed.
