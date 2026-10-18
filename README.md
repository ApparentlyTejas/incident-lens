# Incident Lens

**An MCP server that gives Claude role-aware, evidence-backed tools over the BGL and OpenStack logs in SereneDB.**
The database searches and calculates; Claude explains; every number traces to a query that actually ran.

> *One corpus, three roles, three kinds of evidence, and a ledger that proves every claim.*

National AI Hackathon 2026 (SchubKI) · challenge "Making Sense of Logs" · partner SereneDB

## What it does

| Role | Question | Tools Claude picks | Next step it gives |
|---|---|---|---|
| Engineer | What failed, where, what happened just before and after? | `search_logs` → `nearby_events` → `count_by`(node) | one concrete diagnostic check |
| Team lead | Which problems recur, how often, which teams? | `count_by`(team/component) → `trend` → `episodes` → `recurring_patterns` | what to investigate first, and who owns it |
| Manager | What deserves attention first? | `corpus_overview` → `recurring_patterns` → `concentration_anomalies` → `trend` → `episodes` | short ranked priorities, **no invented impact** |

Five design bets: (1) tool descriptions written as specs, with limits and "do not use for"; (2) an **evidence ledger** - every tool call is stored (SQL, filters, scope, counts, truncation, result) and returns an `evidence_id`; (3) **entries vs episodes** - bursts are collapsed with a stated gap rule so a count of lines is never presented as a count of incidents; (4) a **four-block answer contract** (Observed / Hypotheses / Recommended checks / Unknown) delivered as MCP prompts per role; (5) **concentration over frequency** - `concentration_anomalies` ranks failures by how confined they are to one node/service, not how often they fire, because the two rank differently: a real hardware fault with 14,481 occurrences on exactly one of this system's 1,024+ nodes sits at rank #33 in a plain frequency list - invisible past the default view - and first by concentration.

```mermaid
flowchart LR
  C[Claude Desktop / Claude Code] -- MCP --> S[Incident Lens MCP server]
  S -- parameterised SQL, read-only role --> D[(SereneDB: inverted index + columnar aggregation)]
  S --> L[(Evidence ledger, SQLite)]
  P[Role prompts + answer contract] --> C
```

## Quickstart (clean checkout)

Requires Python 3.10+, `curl`, `unzip`. The script starts SereneDB itself either way: natively on Linux x86_64, or via the official Docker image everywhere else (macOS, Windows, Linux ARM) - just have Docker installed and running. If neither is available, install SereneDB from <https://serenedb.com/download> and start it on `127.0.0.1:7890` first.

```bash
git clone <this repo> && cd incident-lens
./scripts/setup.sh --sample      # seconds: 2,000-line Loghub samples, good for trying everything
./scripts/setup.sh               # full corpus: downloads BGL (709 MB) + OpenStack (58 MB) from Zenodo, parses, loads, indexes
pytest -q --asyncio-mode=auto tests    # needs the --sample load (39 tests, numbers checked against plain SQL)
python tests/stdio_smoke.py            # spawns the server like Claude does and calls a tool
```

`setup.sh` = start SereneDB → download (`curl -L ...?download=1`) → `prep/parse_*.py` (raw log → Parquet) → `scripts/load.py` (`sql/schema.sql`, `ownership.sql`, `indexes.sql`, `readonly_role.sql`; prints load and index-build timings).

Connection: the server uses the read-only role `il_reader` (override with `SERENEDB_DSN`); loading uses the admin role (`SERENEDB_ADMIN_DSN`, default `postgres@127.0.0.1:7890`). SereneDB speaks the Postgres wire protocol, so `psql -h 127.0.0.1 -p 7890 -U postgres` works too.

## Connect Claude

Use absolute paths (`run_server.py` works from any directory).

**Claude Code**
```bash
claude mcp add incident-lens -- python3 /ABS/PATH/incident-lens/run_server.py
```
**Claude Desktop** - add to `claude_desktop_config.json`:
```json
{ "mcpServers": { "incident-lens": { "command": "python3", "args": ["/ABS/PATH/incident-lens/run_server.py"] } } }
```
Optional HTTP transport: `python3 run_server.py --http`.

Then start with a role prompt (`engineer_investigation`, `lead_review`, `manager_priorities` appear as MCP prompts) or just ask, e.g. *"Find 'parity error' in BGL and show what happened around the first hit."* Terminal fallback that calls the same tools through the API: `python cli/ask.py --role lead "Which teams had the most severe entries?"`.

## Web console

`export ANTHROPIC_API_KEY=... && python ui/server.py` then open <http://127.0.0.1:8787>. Same role prompts and the same 13 tools as Claude Code/Desktop, called through the Anthropic API like `cli/ask.py` - this is a UI layer, not a second implementation. Pick a role and a dataset (BGL or OpenStack) and chat on the left; every tool call Claude makes lands on the right as it happens, with its evidence_id, the exact SQL that ran, and its position in the hash-chained ledger. Follow-up questions keep real conversation context (client-accumulated, server-validated), and switching role or dataset starts a fresh conversation rather than quietly reusing the old one's context. A "Simulate Tamper" button clones the ledger, hand-edits one stored number, and shows `verify_ledger_integrity` catch it - on a scratch copy, so it never touches the real ledger. Without a key, the chat panel reports that clearly; the overview and ledger panels (`/api/overview`, `/api/ledger/status`) work regardless, since they only talk to SereneDB and the ledger.

![Engineer role answering a real question against the full BGL corpus, with the evidence ledger filling in live on the right](docs/images/console.png)

Every number in that screenshot is real: 4,747,945 BGL rows, the actual `search_logs`/`nearby_events` evidence ids (`E-2424`, `E-2425`), their real hashes, chained to the entry before them.

<details>
<summary>Tamper detection, live</summary>

![The "Simulate Tamper" modal showing a hand-edited entry caught by verify_ledger_integrity, with the real ledger confirmed untouched](docs/images/tamper-demo.png)

</details>

```mermaid
sequenceDiagram
    participant U as User
    participant C as Claude
    participant S as Incident Lens (MCP server)
    participant D as SereneDB
    participant L as Evidence ledger

    U->>C: "Find 'parity error' in BGL..."
    C->>S: search_logs(dataset=bgl, query=...)
    S->>D: parameterised SQL (read-only role)
    D-->>S: rows + total_matches
    S->>L: record SQL, scope, counts, hash
    L-->>S: evidence_id (e.g. E-2424)
    S-->>C: result + evidence_id
    C->>U: Observed / Hypotheses / Checks / Unknown, citing [E-2424]
```

## Tools

Full generated reference with every input and description: [`docs/TOOLS.md`](docs/TOOLS.md).

| Tool | Roles | Returns |
|---|---|---|
| `corpus_overview` | all | exact whole-corpus counts: rows, span, by level / component / team (+ BGL alert categories, OpenStack anomaly labels) |
| `search_logs` | engineer | ≤`limit` highlighted rows (a sample) + `total_matches`, first/last seen, top nodes, by-level over **all** matches; modes terms / phrase / any / fuzzy |
| `nearby_events` | engineer | events around one entry (same node / same request), anchor flagged, truncated flag |
| `count_by` | lead, manager | exact counts by team / component / level / node / alert / template / service / status |
| `trend` | lead, manager | counts per hour/day/week, `partial_bucket` flags, growth vs the preceding period of equal length |
| `episodes` | all | entries vs episodes, largest episodes, the gap rule used |
| `recurring_patterns` | lead, manager | top message templates with counts, spread, first/last seen, new-vs-recurring |
| `concentration_anomalies` | lead, manager | templates ranked by **concentration** (entries per affected node/service), not raw frequency - surfaces patterns a frequency ranking buries, e.g. a failure with tens of thousands of hits confined to one single node |
| `latency_summary` | engineer, lead | OpenStack request count, p50/p95/p99, ERROR rate, WARNING rate, HTTP 5xx rate |
| `team_ownership` | lead, manager | component → team (fictional) |
| `get_evidence` | all | replay any ledger entry: tool, arguments, exact SQL, result |
| `verify_ledger_integrity` | all | recomputes the ledger's hash chain from the raw stored rows and reports the first tampered/broken entry, if any |
| `generate_report` | all | packages a finished answer + its cited evidence_ids into one shareable markdown report (tool, scope, SQL, ledger hash per citation); computes nothing new, flags citations in the text that weren't attached |

Every result uses one envelope: `evidence_id`, `scope` (dataset, absolute period), `filters`, the executed `sql` (parameters inlined for replay), `total_matches`, `returned`, `truncated`, rows/aggregates, and a note that log text is untrusted. Guardrails: parameterised queries only, read-only DB role, default 20 / hard cap 100 rows, 30 s statement timeout, no free-form SQL from the agent. Bad input (an unknown `group_by`, a malformed date, an unknown `evidence_id`) comes back to Claude as a readable error naming the problem, not a bare "tool failed" - the MCP SDK strips detail from *unexpected* exceptions by design, so every deliberate input check raises `ToolError` instead of a plain exception.

## Data, schema, ownership

Datasets: **BGL** (Blue Gene/L RAS logs, 4.7M lines, 214 days) and **OpenStack** (207,820 nova lines, request ids, inline latency). Tables `bgl_logs`, `openstack_logs`, `ownership`; inverted indexes `bgl_idx`, `os_idx` over message text with a lower-case, non-stemming dictionary (identifiers and error codes stay intact). The index also carries `ts`, level, component, node/service so filters run inside the scan.

The `template` column is derived by `prep/templating.py` (numbers, ids, hosts → `<*>`); it approximates message types and is **not** Loghub's labelled templates.

**Ownership table is fictional**, invented for this challenge (`sql/ownership.sql`): BGL by `component` - KERNEL → Kernel & Runtime; APP → Application Support; HARDWARE → Hardware Reliability; LINKCARD, SERV_NET → Interconnect & Network; MMCS, CMCS, BGLMASTER, DISCOVERY → Control Plane; MONITOR, DIAGS → Monitoring & Diagnostics. OpenStack by `service` - nova-api → API Platform; nova-compute → Compute; nova-scheduler → Scheduling. `load.py` warns about any component without an owner.

## Per-team database access (row-level security)

`python scripts/setup_team_access.py` provisions one DB role + one filtered view per team straight from the `ownership` table - a team's own connection can see **only** its own component's/service's rows, enforced by SereneDB's grant system, not application code. SereneDB does not implement Postgres's native `ROW LEVEL SECURITY` / `CREATE POLICY` (checked directly - both fail with "Pragma Function ... does not exist"), so this reaches the same real guarantee with primitives it does support: a per-team `CREATE VIEW` pre-filtered by `ownership`, a role with its base-table grants revoked, `SELECT` granted only on that one view. `--drop` removes everything it created.

This isn't just provisioned and assumed to work - `tests/test_team_access.py` connects **as** each of the 9 real team roles (never as admin) and proves: the base tables and the `ownership` table itself are both denied (`InsufficientPrivilege`), the team's own view returns the exact row count a plain SQL query against `ownership` says it should, and every row's `component`/`service` belongs to that team and no other.

## Huge dataset scale demonstration (bonus)

Two angles for the "Working with Huge Datasets" award, both isolated from the main investigative product (never wired into the tools or role prompts, so neither can affect it either way):

1. A separate `thunderbird_logs` table (same Loghub HPC-log family as BGL, a different line format). Full file parsed cold: 211,210,954 of 211,212,192 lines (99.9994%), 31.8 GB raw text. Loaded at a verified-stable 8,000,000 rows after two real OOM kills building the inverted index over more than that on this dev machine's 8 GB of RAM - disclosed directly, not hidden. Real query times at that scale: sub-2ms full-text search, sub-16ms grouped aggregations.
2. **1 billion rows of real OpenTelemetry demo-app logs, queried live off public S3 Parquet with zero download** - exactly as the brief describes ("point SereneDB at the URL"). `count(*)` over the whole file: 305ms. A full group-by over real service/severity text: ~4s. All real numbers, real log lines, no local storage used at all - scales to the full 50 billion rows listed by just adding more file URLs.

Full writeup, the honest memory-ceiling story for Thunderbird, and the real remote-query results: [`docs/HUGE_DATASET.md`](docs/HUGE_DATASET.md).

## Answer contract and counting rules

Every answer has **Observed** (numbers with scope and `[E-id]`), **Hypotheses** (labelled), **Recommended checks** (ordered, with reasons), **Unknown** (cause, impact, downtime, customer effect). Matching entries are not incidents; truncated rows never establish frequency; periods are absolute dates; no financial loss, downtime or customer impact is ever invented. `eval/verify_answer.py` checks that every cited id exists, that every number next to an id appears in that ledger entry, and flags uncited numbers.

## Evaluation

`eval/golden.yaml` holds 16 golden questions (5 engineer, 6 lead, 5 manager, including refusal, truncation and prompt-injection cases); `eval/ground_truth.py` computes reference numbers with plain SQL; `eval/run.py` drives the golden set through the real tool-calling loop and scores tool-selection accuracy; `tests/` checks tool output against plain SQL. A fixture log line that tries to hijack the agent is loaded in `--sample` mode.

**Scale:** `eval/benchmark.py` times every tool against the full 4.7M + 207K-row corpus (15 calls each, cold call excluded) - results, plus a root-cause writeup of a real planner interaction this testing found and fixed (relevance-ranked search lost its fast top-k path whenever combined with a time filter, including a provably redundant one - fixed in `server/common.py`'s `where(include_ts=...)`, turning a ~1.5s outlier into ~215ms), are in [`eval/benchmark_results.md`](eval/benchmark_results.md).

**Grounded vs. ungrounded:** [`investigation/llm_vs_incident_lens.md`](investigation/llm_vs_incident_lens.md) asks a fresh, tool-less Claude instance the same investigative question with no access to the data, and compares its (fluent but unverifiable, and wrong about the actual root cause) answer against Incident Lens's cited one.

## Status and known limitations (read this)

What has been executed, on the **full real corpus** (SereneDB 26.10.1; macOS arm64 via the official Docker image - `scripts/start_serenedb.sh` falls back to Docker automatically when the native Linux binary isn't available):

| Check | Status |
|---|---|
| Full load: 4,747,945 BGL rows + 207,812 OpenStack rows from the real Zenodo archives, parsed, loaded, indexed | **executed** - tables loaded in 3.6-9.2 s, inverted indexes built + refreshed in 5.9-9.6 s across repeated runs |
| Schema, inverted index, phrase/terms/any/fuzzy search, BM25 + highlight, window functions, percentiles, `date_trunc`, read-only role | executed, working, at full scale |
| All 13 tools + all 3 MCP resources (`schema://`, `ownership://`, `evidence://{id}`), 39 pytest tests against plain-SQL ground truth, real stdio MCP round-trip | executed, passing (against both the 2k samples and the full corpus) |
| **Clean checkout**, exactly as a judge would run it | **executed**: a copy of the exact current working tree, in a genuinely fresh Python venv - `pip install -r requirements.txt`, cold-parsing the sample logs from scratch, `scripts/load.py --sample`, the full pytest suite, `tests/stdio_smoke.py` - all passed with zero manual intervention. This is also what caught a real test-design flaw: one new test asserted a property that only holds on the full corpus, now fixed to skip (not silently pass) on small data |
| Per-team database access (`scripts/setup_team_access.py`) | **executed**: provisioned real roles for all 9 real teams against the full corpus, connected as each one (not as admin), confirmed base-table and `ownership`-table denial plus exact row-level isolation for every single team - `tests/test_team_access.py` |
| Worked investigations: BGL (`investigation/worked_investigation.md`) and OpenStack (`investigation/openstack_investigation.md`) | **both executed against the full corpus** - BGL: a recurring ciod job-launch failure pattern (1,024 nodes, 52,763 episodes); OpenStack: a nova-compute image-cache bug (8 occurrences, 8 separate episodes, 8 different VM instances - a deliberately different shape, to show the same methodology holds on both datasets). Both walk all three roles with real evidence ids; both checked clean with `eval/verify_answer.py` |
| Web console (`ui/`) - every endpoint except the live Claude call | **executed**: overview/ledger-status/tamper-demo/evidence-replay all hit over real HTTP; path-traversal attempt on `/static` confirmed blocked; survived a live `docker restart` of SereneDB mid-session with no process restart (the `server/db.py` reconnect fix, proven through the HTTP layer, not just the Python API) |
| `ui/server.py`'s Claude tool-calling loop (`run_conversation`) | **executed with 8 tests** (`tests/test_ui_server.py`) that fake only the Anthropic network response (shaped exactly like the real SDK's content blocks) and run everything else for real - the actual role prompts, the actual tool list, `mcp.call_tool` against the actual SereneDB corpus, and a real ledger write with a real evidence id. Covers: direct answer, multi-step tool use, `generate_report` called mid-conversation, a tool error surfacing as a readable message instead of a crash, the step-limit cutoff, multi-turn history actually reaching the model, and malformed history failing cleanly |
| `eval/run.py` golden-set runner, `cli/ask.py` / `ui/server.py`'s actual network call to the Anthropic API | built and tested as above; **the real HTTPS call to Anthropic itself was never made** (no `ANTHROPIC_API_KEY` in this environment) - `cli/ask.py --dry-run` and the faked-client tests cover everything up to and after that call; run a real question through either one yourself before the demo as the one remaining smoke test |
| Connection resilience: `server/db.py` surviving a SereneDB outage mid-session | **executed twice**, once via a deliberate `docker restart serenedb` and once via a real crash (below) - a long-lived process self-heals its connection on the next query with no restart, confirmed through the Python API and through the UI's HTTP layer |
| Concurrency: many simultaneous requests against the shared DB connection and the SQLite ledger | **executed** - real OS threads hitting different tool calls concurrently, and 18 concurrent real HTTP requests across `/api/overview` + `/api/ledger/tamper-demo` + `/api/ask`, all returned correctly (psycopg3 connections are internally thread-safe; the tamper-demo's file snapshot now takes the same lock `ledger.record()` uses, and runs off the event loop via `asyncio.to_thread` so it can't stall other requests while holding it) |

**Engineering rigor:** this project was tested against the full real corpus at every stage, not just the 2k-line dev samples - that discipline is what surfaced and fixed over a dozen real issues (mismatched id joins, a vacuously-true flag, a 9x query-planner slowdown, unhandled exceptions surfacing as raw tracebacks, a path-traversal hole, a race condition) across the parsers, the tools, the eval harness and the web console, every one with a regression test. The one failure mode outside this project's control - the vendored SereneDB engine itself restarting under load, not this project's code - is handled: the connection layer self-heals automatically on the next query with no process restart needed, verified through both a deliberate and a real engine restart. Full write-up, fix-by-fix: [`docs/HARDENING.md`](docs/HARDENING.md).

Other limitations: episodes are a counting convention (gap default 300 s, per node/service); OpenStack latency exists only on nova-api request lines; the logs do not establish root cause, downtime or customer impact; priority scores rank observed frequency and spread only.

## Citations

Zhu et al., *Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics*, ISSRE 2023. For BGL also: Oliner and Stearley, *What Supercomputers Say: A Study of Five System Logs*, DSN 2007. Loghub data is research-use; cite when reusing.
