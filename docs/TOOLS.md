# Incident Lens tools (generated from the MCP server; do not edit)

## `corpus_overview`

Whole-corpus summary: exact row counts, time span, counts by level, component/service and mapped team (plus BGL alert categories / OpenStack labelled anomalies).
USE FIRST to learn the data scope, available levels and components, and for 'across everything' or 'by dataset and team' questions (manager, lead).
Inputs: dataset (optional; omit for both). Returns: exact counts over every row, no sampling.
DO NOT use for finding specific messages (use search_logs) or time-bounded counts (use count_by).

**Inputs**

- `dataset`: any. Omit for both datasets.

## `search_logs`

Full-text search over log messages (SereneDB inverted index, BM25-ranked, highlighted snippets) with time/level/component/node filters. ENGINEER's starting point: 'find the error'.
Inputs: dataset, query, mode (terms|phrase|any|fuzzy), optional from_ts/to_ts, levels or severity, component, node (bgl), service (openstack), order (relevance|time), limit (default 20, max 100).
Returns: up to `limit` rows (a SAMPLE), plus total_matches and a summary over ALL matches: first_seen, last_seen, distinct nodes, top nodes, counts by level. truncated=true means more matches exist.
Frequency questions: quote total_matches/summary, never the number of rows. Entries are not incidents - use `episodes` for occurrences.
DO NOT use for grouped counts or trends over many entries (count_by, trend), and do not paste rows as evidence of how often something happens.

**Inputs**

- `dataset` (required): one of bgl/openstack. bgl = Blue Gene/L supercomputer logs (Jun 2005-Jan 2006, nodes, RAS severity levels, labelled alerts). openstack = nova-api/compute/scheduler logs (request ids, latency).
- `query` (required): string. Words or phrase to find in the log message, e.g. 'parity error' or 'ciod: Error'.
- `mode`: one of terms/phrase/any/fuzzy. terms: all words must appear (default). phrase: exact adjacent phrase. any: at least one word. fuzzy: tolerate typos (Levenshtein).
- `from_ts`: any. Period start, inclusive, ISO like '2005-06-03' or '2005-06-03 15:00:00'. Omit for the dataset's first log. Always an absolute date; the logs are historical.
- `to_ts`: any. Period end, EXCLUSIVE, ISO date/time. Omit for the dataset's last log.
- `levels`: any. Exact log levels to keep, e.g. ['FATAL','ERROR']. Overrides `severity`. Call corpus_overview to see the levels present.
- `severity`: one of all/severe. 'severe' keeps the dataset's severe levels (bgl: FATAL, FAILURE, SEVERE, ERROR; openstack: ERROR, CRITICAL, TRACE). Ignored if `levels` is set.
- `component`: any. Exact component, e.g. KERNEL.
- `node`: any. bgl only: exact node id.
- `service`: any. openstack only: nova-api | nova-compute | nova-scheduler.
- `order`: one of relevance/time. relevance = BM25; time = oldest first.
- `limit`: integer. Rows to return, 1-100, default 20.
- `fuzzy_distance`: integer. Max edits per word in fuzzy mode (1-2).

## `nearby_events`

Events around one log entry: what happened just before and after. ENGINEER, after search_logs.
Inputs: dataset, log_id (the `id` from a search hit), before_seconds / after_seconds (default 60/60, max 86400), scope, limit (default 40, max 100).
Scope: bgl auto/same_node = same node only; any_node = every node in the window. openstack auto = same request_id if the entry has one, else same service; also same_service | any.
Returns: the anchor entry, events ordered by time (anchor flagged), total events in the window and truncated flag (if truncated the events closest in time are kept).
Events are context, not proof of causation. DO NOT use to count occurrences.

**Inputs**

- `dataset` (required): one of bgl/openstack. bgl = Blue Gene/L supercomputer logs (Jun 2005-Jan 2006, nodes, RAS severity levels, labelled alerts). openstack = nova-api/compute/scheduler logs (request ids, latency).
- `log_id` (required): integer. `id` of the anchor entry from search_logs rows.
- `before_seconds`: integer. 
- `after_seconds`: integer. 
- `scope`: one of auto/same_node/any_node/same_request/same_service/any. See tool description.
- `limit`: integer. 

## `count_by`

Exact counts grouped by component, team, level, node/service, alert, template etc. over the WHOLE stated scope (aggregated in the database, never a sample). TEAM LEAD and MANAGER: 'which components/teams have the most problems'.
Inputs: dataset, group_by, optional from_ts/to_ts, optional text query (+mode), levels or severity, component, node, service, alert, limit (default 25).
group_by: team (via the FICTIONAL ownership table), component, level, node, alert, template, type (bgl); service, component, level, http_status, http_method, template, team (openstack).
Returns: rows of name/entries/share_pct, total_matches, groups_total, truncated. Counts are log ENTRIES, not incidents - pair with `episodes` for occurrence claims.
DO NOT use to read message text (search_logs) or to see change over time (trend).

**Inputs**

- `dataset` (required): one of bgl/openstack. bgl = Blue Gene/L supercomputer logs (Jun 2005-Jan 2006, nodes, RAS severity levels, labelled alerts). openstack = nova-api/compute/scheduler logs (request ids, latency).
- `group_by` (required): string. team | component | level | node | alert | template | type (bgl); team | service | component | level | http_status | http_method | template (openstack).
- `from_ts`: any. Period start, inclusive, ISO like '2005-06-03' or '2005-06-03 15:00:00'. Omit for the dataset's first log. Always an absolute date; the logs are historical.
- `to_ts`: any. Period end, EXCLUSIVE, ISO date/time. Omit for the dataset's last log.
- `query`: any. Optional text filter on the message.
- `mode`: one of terms/phrase/any/fuzzy. terms: all words must appear (default). phrase: exact adjacent phrase. any: at least one word. fuzzy: tolerate typos (Levenshtein).
- `levels`: any. Exact log levels to keep, e.g. ['FATAL','ERROR']. Overrides `severity`. Call corpus_overview to see the levels present.
- `severity`: one of all/severe. 'severe' keeps the dataset's severe levels (bgl: FATAL, FAILURE, SEVERE, ERROR; openstack: ERROR, CRITICAL, TRACE). Ignored if `levels` is set.
- `component`: any. 
- `node`: any. 
- `service`: any. 
- `alert`: any. bgl only: labelled alert category.
- `limit`: integer. 

## `trend`

Counts per time bucket (hour|day|week) over a stated absolute period, with growth against the immediately preceding period of equal length. TEAM LEAD and MANAGER: 'is it getting worse?'.
Inputs: dataset, bucket, optional from_ts/to_ts, optional text query (+mode), levels/severity, component, node, service, alert, optional group_by (component|team|level|... top groups per bucket, top_groups default 5).
Returns: per-bucket entries with change_vs_previous_bucket_pct and a partial_bucket flag for edge buckets, plus period_comparison {current, prior, growth_pct}. growth_pct is null if the prior period has no data. Exact counts, not a sample.
DO NOT use for message text or for one-off totals (count_by).

**Inputs**

- `dataset` (required): one of bgl/openstack. bgl = Blue Gene/L supercomputer logs (Jun 2005-Jan 2006, nodes, RAS severity levels, labelled alerts). openstack = nova-api/compute/scheduler logs (request ids, latency).
- `bucket`: one of hour/day/week. 
- `from_ts`: any. Period start, inclusive, ISO like '2005-06-03' or '2005-06-03 15:00:00'. Omit for the dataset's first log. Always an absolute date; the logs are historical.
- `to_ts`: any. Period end, EXCLUSIVE, ISO date/time. Omit for the dataset's last log.
- `query`: any. 
- `mode`: one of terms/phrase/any/fuzzy. terms: all words must appear (default). phrase: exact adjacent phrase. any: at least one word. fuzzy: tolerate typos (Levenshtein).
- `levels`: any. Exact log levels to keep, e.g. ['FATAL','ERROR']. Overrides `severity`. Call corpus_overview to see the levels present.
- `severity`: one of all/severe. 'severe' keeps the dataset's severe levels (bgl: FATAL, FAILURE, SEVERE, ERROR; openstack: ERROR, CRITICAL, TRACE). Ignored if `levels` is set.
- `component`: any. 
- `node`: any. 
- `service`: any. 
- `alert`: any. 
- `group_by`: any. Optional series split, e.g. component or team.
- `top_groups`: integer. 

## `episodes`

Entries vs episodes: collapses bursts of matching entries on the same node (bgl) or service (openstack) when consecutive entries are within gap_seconds (default 300). ALL ROLES, whenever an answer talks about how many TIMES something happened.
Inputs: dataset, optional from_ts/to_ts, optional text query (+mode), levels/severity, component, node, service, alert, gap_seconds (default 300), top (default 10).
Returns: entries, episodes, largest_episode_entries, the top episodes (start, end, entries, duration), and the gap rule used. An episode is a counting convention, not a real incident.
Always report BOTH numbers and the gap rule. DO NOT use for message text or grouped team counts.

**Inputs**

- `dataset` (required): one of bgl/openstack. bgl = Blue Gene/L supercomputer logs (Jun 2005-Jan 2006, nodes, RAS severity levels, labelled alerts). openstack = nova-api/compute/scheduler logs (request ids, latency).
- `from_ts`: any. Period start, inclusive, ISO like '2005-06-03' or '2005-06-03 15:00:00'. Omit for the dataset's first log. Always an absolute date; the logs are historical.
- `to_ts`: any. Period end, EXCLUSIVE, ISO date/time. Omit for the dataset's last log.
- `query`: any. 
- `mode`: one of terms/phrase/any/fuzzy. terms: all words must appear (default). phrase: exact adjacent phrase. any: at least one word. fuzzy: tolerate typos (Levenshtein).
- `levels`: any. Exact log levels to keep, e.g. ['FATAL','ERROR']. Overrides `severity`. Call corpus_overview to see the levels present.
- `severity`: one of all/severe. 'severe' keeps the dataset's severe levels (bgl: FATAL, FAILURE, SEVERE, ERROR; openstack: ERROR, CRITICAL, TRACE). Ignored if `levels` is set.
- `component`: any. 
- `node`: any. 
- `service`: any. 
- `alert`: any. 
- `gap_seconds`: integer. Max seconds between consecutive entries within one episode. Default 300.
- `top`: integer. 

## `recurring_patterns`

Most frequent message templates (message types with numbers/ids/hosts normalised to <*>) in a period, with counts, spread across nodes/components, first/last seen, active days, and whether the template first appeared inside the period. TEAM LEAD and MANAGER: 'which problems recur'.
Inputs: dataset, optional from_ts/to_ts, levels/severity, component, node, service, alert, limit (default 15).
Returns: ranked templates with entries/share_pct, templates_total, returned_templates_cover_pct, truncated. Templates come from a simple normaliser, not Loghub's labelled templates.
DO NOT use to read individual events (search_logs).

**Inputs**

- `dataset` (required): one of bgl/openstack. bgl = Blue Gene/L supercomputer logs (Jun 2005-Jan 2006, nodes, RAS severity levels, labelled alerts). openstack = nova-api/compute/scheduler logs (request ids, latency).
- `from_ts`: any. Period start, inclusive, ISO like '2005-06-03' or '2005-06-03 15:00:00'. Omit for the dataset's first log. Always an absolute date; the logs are historical.
- `to_ts`: any. Period end, EXCLUSIVE, ISO date/time. Omit for the dataset's last log.
- `levels`: any. Exact log levels to keep, e.g. ['FATAL','ERROR']. Overrides `severity`. Call corpus_overview to see the levels present.
- `severity`: one of all/severe. 'severe' keeps the dataset's severe levels (bgl: FATAL, FAILURE, SEVERE, ERROR; openstack: ERROR, CRITICAL, TRACE). Ignored if `levels` is set.
- `component`: any. 
- `node`: any. 
- `service`: any. 
- `alert`: any. 
- `limit`: integer. 

## `latency_summary`

OpenStack-only request latency and error summary: request count, avg/p50/p95/p99/max latency, ERROR/CRITICAL entry rate, WARNING rate and HTTP 5xx rate, grouped by service (default), component, http_status, http_method, hour or day.
ENGINEER and TEAM LEAD: 'which service is slow or erroring'. Inputs: optional from_ts/to_ts, service, group_by, levels, limit.
Latency exists only on nova-api request lines (inline `time:`); metric_definitions in the result say exactly how each rate is computed. Exact aggregates, not a sample.
DO NOT use for BGL (it has no latency) or for message text.

**Inputs**

- `from_ts`: any. Period start, inclusive, ISO like '2005-06-03' or '2005-06-03 15:00:00'. Omit for the dataset's first log. Always an absolute date; the logs are historical.
- `to_ts`: any. Period end, EXCLUSIVE, ISO date/time. Omit for the dataset's last log.
- `service`: any. 
- `group_by`: one of service/component/http_status/http_method/hour/day/none. 
- `levels`: any. Exact log levels to keep, e.g. ['FATAL','ERROR']. Overrides `severity`. Call corpus_overview to see the levels present.
- `limit`: integer. 

## `team_ownership`

Component -> team ownership mapping (FICTIONAL, invented for this challenge). TEAM LEAD and MANAGER: 'who owns this?'.
Inputs: dataset (optional), component (optional; for openstack the key is the service). Returns: dataset/component/team rows. Use it to name an owner for a recommendation.

**Inputs**

- `dataset`: any. 
- `component`: any. 

## `get_evidence`

Replay any earlier tool result from the evidence ledger by its evidence_id (e.g. 'E-0007'): the tool, arguments, the exact SQL that ran, counts, truncation flag and result. ALL ROLES, to verify or re-quote a number. Does not run new queries.

**Inputs**

- `evidence_id` (required): string. Id like 'E-0007' returned by any tool.
