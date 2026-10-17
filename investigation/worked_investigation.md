# Worked investigation: <pattern name>   (TEMPLATE - fill in from real runs on the FULL data)

> Rules: every number below must come from a tool call whose evidence id is cited. Facts, hypotheses, checks and unknowns stay separate.
> Pick the pattern with `investigation/find_candidates.sql` (hours 6-18). Data scope: BGL full corpus (period: <from> to <to>); OpenStack appears in the whole-corpus count.

## 0. Why this pattern
<one sentence: searchable text, several episodes, more than one node, visible trend>

## 1. Engineer
**Question:** <...>
**Tool calls:** `search_logs(...)` [E-....] -> `nearby_events(...)` [E-....] -> `count_by(group_by=node)` [E-....]
**Executed queries:** (copy from `get_evidence`)
**Answer (four blocks):** Observed / Hypotheses / Recommended check / Unknown

## 2. Team lead
**Question:** <...>
**Tool calls:** `count_by(team)`, `trend(week)`, `episodes`, `recurring_patterns`
**Answer:** Observed (entries vs episodes, gap rule, period, owner from the FICTIONAL ownership table) / Hypotheses / What to investigate first / Unknown

## 3. Manager
**Question:** <...>
**Tool calls:** `corpus_overview`, `recurring_patterns`, `trend`, `episodes`
**Priority table** (priority = episodes x components_affected x (1 + growth)); a heuristic of observed frequency and spread, not an impact estimate.
**Answer:** short ranked list + actions. No downtime, cost or customer-impact claims.

## 4. What the data does and does not establish
- Establishes: <...>
- Does not establish: root cause, impact, downtime, customer effect.
