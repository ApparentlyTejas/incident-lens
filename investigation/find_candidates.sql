-- Hour 6-18: shortlist real patterns for the worked investigation (PRD section 5 criteria):
--   (1) searchable text  (2) several distinct episodes, not one burst  (3) more than one node/component  (4) visible trend.
-- Run on the FULL data:  python -c "..." or psql -h 127.0.0.1 -p 7890 -U postgres -f investigation/find_candidates.sql
WITH base AS (
  SELECT template, node, component, ts, date_trunc('week', ts) AS wk,
         ts - lag(ts) OVER (PARTITION BY template, node ORDER BY ts) AS gap
  FROM bgl_logs
  WHERE level IN ('FATAL','FAILURE','SEVERE','ERROR')
), marked AS (
  SELECT *, CASE WHEN gap IS NULL OR gap > INTERVAL '300 seconds' THEN 1 ELSE 0 END AS new_ep FROM base
)
SELECT template,
       count(*)                       AS entries,
       sum(new_ep)                    AS episodes,           -- per (template, node), gap 300 s
       count(DISTINCT node)           AS distinct_nodes,
       count(DISTINCT component)      AS distinct_components,
       count(DISTINCT wk)             AS active_weeks,
       min(ts)                        AS first_seen,
       max(ts)                        AS last_seen
FROM marked
GROUP BY template
HAVING sum(new_ep) >= 10 AND count(DISTINCT node) >= 3 AND count(DISTINCT wk) >= 3
ORDER BY episodes DESC, active_weeks DESC
LIMIT 25;
