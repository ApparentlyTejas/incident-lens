# Benchmark: 4,955,757 rows (bgl_logs + openstack_logs), SereneDB, 20 calls per tool

| tool call | median ms | min ms | max ms | p95 ms | matches/rows touched | evidence |
|---|---|---|---|---|---|---|
| corpus_overview (whole corpus, both datasets) | 151.3 | 110.3 | 318.5 | 296.2 | 4955757 | `E-0348` |
| search_logs - common term, full period | 1523.1 | 1429.9 | 1749.1 | 1729.3 | 112584 | `E-0369` |
| search_logs - fuzzy mode | 240.9 | 208.1 | 313.9 | 262.7 | 121498 | `E-0390` |
| nearby_events - any_node, +-60s | 14.2 | 9.3 | 40.5 | 34.0 | 32 | `E-0411` |
| count_by - node (1024 groups) | 75.5 | 51.8 | 317.2 | 198.2 | 112584 | `E-0432` |
| count_by - team, whole corpus, severe only | 66.2 | 44.5 | 166.4 | 99.3 | 988783 | `E-0453` |
| trend - week buckets, full 7-month span | 16.6 | 12.6 | 30.8 | 25.4 | 4747945 | `E-0474` |
| episodes - whole corpus, severe only | 572.4 | 502.7 | 961.8 | 842.9 | 988783 | `E-0495` |
| recurring_patterns - whole corpus, severe only | 154.6 | 111.1 | 378.0 | 291.9 | 988783 | `E-0516` |
| latency_summary - whole openstack corpus | 16.8 | 14.0 | 64.9 | 41.1 | 207812 | `E-0537` |

## Resource usage during this run

`docker stats` on the SereneDB container, sampled mid-run: ~850MB resident memory (22% of the 3.8GB container limit), 2% CPU on this snapshot (CPU is bursty - near 0% between calls, briefly higher during each query). No tuning or warm-up beyond one excluded cold call per tool; every number above is the database actually scanning the full 4.7M/207K-row tables, not a cached or pre-aggregated result.

## Why `search_logs` in relevance mode is the one outlier

Every other tool is sub-600ms even over the full corpus; `search_logs` in its default relevance-ranked mode is ~1.5s - roughly 9x slower than the identical query in `order=time` mode (169ms, measured directly). `EXPLAIN` on the underlying query shows why: SereneDB's planner estimates the combined "phrase match AND absolute time range" filter at ~950,000 candidate rows, nearly 9x the ~112,584 that actually match - and that misestimate specifically affects the plan chosen when `ORDER BY BM25(...)` is also present. Isolated timing confirms it: the identical count query with no time bound runs in 4ms; the same count query with the (always-present, since every tool call has a scope) time bound added runs in 93ms; adding BM25 ranking on top of that is what produces the full ~1.5s. This is a real, reproducible planner characteristic at this data size, not a fluke - and it is reported here rather than quietly worked around, in keeping with this project's rule against hiding what it does not fully control. Engineers who don't need relevance ranking (e.g. time-ordered browsing) get the fast path for free via `order=time`.
