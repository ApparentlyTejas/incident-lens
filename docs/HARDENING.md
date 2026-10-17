# Testing & hardening log

This project was tested against the full real corpus at every stage, not just the 2k-line dev
samples - that's what surfaced the issues below, and every one of them has a regression test.
Linked from the README's "Status and known limitations" section.

## SereneDB stability (vendor engine, not this project's code)

Under sustained query load, the vendored SereneDB binary crashed (SIGSEGV) a handful of times
during testing and restarted cleanly every time with zero data loss - the engine itself is
closed-source, so this isn't something to fix here. What **is** fixed and verified, twice
independently (once via a deliberate `docker restart`, once via a real crash): `server/db.py`'s
connection layer self-heals on the very next query with no process restart needed, confirmed
through both the raw Python API and the web console's HTTP layer. The dashboard
(`ui/static/index.html`) also retries with backoff instead of freezing on a failed poll. If this
happens live: give it a few seconds, or run `docker restart serenedb` - nothing else needs touching.

## Concurrency

Verified with real OS threads hitting different tool calls simultaneously, and 18 concurrent real
HTTP requests spanning `/api/overview`, `/api/ledger/tamper-demo` and `/api/ask` at once - all
returned correctly. psycopg3 connections are internally thread-safe; the tamper-demo's ledger
snapshot takes the same lock `ledger.record()` uses and runs off the event loop via
`asyncio.to_thread` so it can't stall other requests while holding it.

## Issues found and fixed

- **OpenStack anomaly labelling** was joining the wrong id type (instance ids against request ids), silently labelling almost nothing as anomalous. Fixed to label by source file.
- **BGL parser** dropped lines with no message text and mis-shifted fields on the small number of lines with no specific node, undercounting FATAL entries. Fixed and verified against the full 4.7M-line file.
- A handful of raw BGL lines carry literal binary corruption in the source file itself - now detected and skipped rather than guessed at.
- **`nearby_events`** silently substituted a different `scope` whenever an explicit one didn't apply to the dataset (e.g. an OpenStack-only scope on a `bgl` call), instead of erroring on the mismatch. Fixed to raise a clear error; this tool had no test coverage before, it does now.
- **`recurring_patterns`'s `new_in_period` flag** was `True` for every single template - including the corpus's oldest, most persistent ones - whenever no explicit `from_ts` was given, because "first appeared on/after the dataset's own start" is vacuously true of everything. Fixed to report `null` unless a bounded period is given; this tool had no test coverage before either.
- **`search_logs` relevance-ranked mode** was ~9x slower than time-ordered mode due to a real SereneDB planner interaction (losing its fast top-k scoring path whenever a redundant time filter was combined with `ORDER BY` relevance). Fixed by dropping the filter when it's provably a no-op - full root-cause writeup in [`eval/benchmark_results.md`](../eval/benchmark_results.md).
- **OpenStack's configured "severe" levels listed `TRACE`**, a level that never actually occurs in the real data (the real levels are INFO/WARNING/ERROR/CRITICAL). Harmless by luck, but the false claim was baked into the tool schema Claude sees on every call. Fixed.
- **`eval/ground_truth.py`'s plain-SQL reference for golden question E1** used a substring match instead of the tokenized match the real tool uses, over-counting by 47 rows - it would have made a correct answer look wrong. Fixed to match `tests/test_tools.py`'s already-correct approach.
- **`eval/run.py`** duplicated `cli/ask.py`'s tool-calling loop, including the same unhandled-exception gap - but worse, since one bad question among 15 would crash the whole run and lose every other question's results (only written at the end). Fixed with per-question isolation, verified with a fake client where one question fails and the rest still make it into the report.
- **`scripts/load.py`'s sample-fixture loader** and **`eval/benchmark.py`** both had the same operator-precedence bug (`X or Y or "KEYWORD" in z` not parenthesized the way it looked), which could force-include and try to execute an empty SQL statement. Fixed in both places.
- **`generate_report`, `cli/ask.py`, and `ui/server.py`'s chat loop** all had the same class of gap: inputs that bypass a schema layer (a plain string where a list was expected, a missing API key, a tool call failing mid-investigation) surfaced as raw unhandled tracebacks instead of a readable message. Fixed across all three, each with its own regression test.
- A real **path-traversal hole** in the web console's static-file route, and a **race condition** where the tamper-demo endpoint briefly mutated a global config path that a concurrent real request could read mid-swap. Both fixed.
- The **MCP resource surface had zero test coverage** (only the tools were tested) - and reading it found a real inconsistency: `evidence://{id}` silently returned the literal string `"null"` for an unknown id instead of erroring, while the equivalent `get_evidence` *tool* already raised a helpful error for the identical case. Fixed to raise `ResourceError` with the same message shape.
- The injection-fixture test's hardcoded id (`999999`) collided with a genuine row in the full corpus (`bgl_logs.id` runs 1..4,747,945 with no primary key to catch a collision) - found by the clean-checkout run above being honest about what it was testing. Fixed to a negative, collision-proof id, and the test itself now skips with a stated reason when the fixture isn't loaded, instead of failing against data where it was never meant to exist.
- **The web console had no way to actually switch datasets** - the chat request hardcoded `dataset:'bgl'` even though OpenStack presets were visible in the UI, and the role prompt literally reads "investigate a failure in `{dataset}` logs." An OpenStack question would silently run against a system prompt still framed around BGL. Fixed by adding a real BGL/OpenStack toggle that resets conversation history on switch (same reasoning as a role switch - the system prompt's framing changed, so old history would be inconsistent with it) and threads the selected dataset through to the request body.
- A new test for `concentration_anomalies` asserted a property ("this finding ranks far below where a frequency list would show it") that's a real, demonstrated fact about the full corpus's actual data, not a logical guarantee of the algorithm itself - the 2k-line `--sample` load didn't reproduce it (not because anything was wrong, just not enough data for that specific shape). Caught by actually running a clean-checkout test against the sample load rather than assuming it; fixed to `pytest.skip` with a stated reason on small datasets instead of silently asserting something scale-dependent as if it always held.

## Clean-checkout verification

Copied the exact current working tree (respecting `.gitignore`, via `git ls-files`) into a separate directory, created a genuinely fresh Python virtualenv, and ran the real path start to finish: `pip install -r requirements.txt`, cold-parsing the sample logs from scratch (not reusing already-parsed Parquet files), `scripts/load.py --sample`, the full pytest suite, and `tests/stdio_smoke.py` (a real MCP client over stdio). Everything passed with zero manual fixes needed - the one thing it did catch is the test-design flaw above, which is exactly what this kind of test is for.

## Per-team database access

`scripts/setup_team_access.py` provisions one role + one filtered view per team from the real `ownership` table. SereneDB has no native `ROW LEVEL SECURITY`/`CREATE POLICY` support (confirmed directly - both raise "Pragma Function ... does not exist"), so this reaches the same guarantee through views + revoked base-table grants, which SereneDB does support. Verified for all 9 real teams by connecting **as** each restricted role (never as admin): the base tables and `ownership` itself are both denied, and each team's view returns exactly the row count a plain SQL query says it should, with every returned row's component/service belonging to that team and no other. `tests/test_team_access.py` has the full proof.

Every fix above has a passing regression test. Full suite: 24 tool tests + 6 UI/chat-loop tests, run against both the sample and full-corpus loads.
