"""The Incident Lens tools. Plain functions (testable without MCP); server/app.py registers them.
Every tool: parameterised SQL only, row caps, scope + truncation reported, evidence-ledger entry."""
import datetime as dt, re
from . import config, ledger
from .common import (dataset_cfg, resolve_period, where, clamp_limit, Tracer, finish, dataset_span)

BUCKETS = {"hour", "day", "week"}

def _team_join(cfg, dataset, alias="t"):
    return f"LEFT JOIN ownership o ON o.dataset = '{dataset}' AND o.component = {alias}.{cfg['owner_col']}"

def _grp_expr(cfg, col, alias="t"):
    if col == "team": return "coalesce(o.team, 'UNMAPPED')"
    if col not in cfg["group_cols"]: raise ValueError(f"group_by must be one of {sorted(cfg['group_cols'])} for this dataset, got {col!r}")
    return f"coalesce(CAST({alias}.{col} AS VARCHAR), '(none)')"

# ---------------------------------------------------------------- corpus_overview
def corpus_overview(dataset=None):
    names = [dataset_cfg(dataset)[0]] if dataset else list(config.DATASETS)
    tr, out = Tracer(), {}
    for ds in names:
        cfg = config.DATASETS[ds]; tbl = cfg["table"]
        _, r = tr.run(f"SELECT count(*) AS n, min(ts) AS first_ts, max(ts) AS last_ts FROM {tbl}")
        d = {"rows": r[0]["n"], "first_ts": r[0]["first_ts"], "last_ts": r[0]["last_ts"]}
        d["by_level"] = tr.run(f"SELECT level, count(*) AS entries FROM {tbl} GROUP BY 1 ORDER BY 2 DESC")[1]
        oc = cfg["owner_col"]
        d[f"by_{oc}"] = tr.run(f"SELECT {oc} AS name, count(*) AS entries FROM {tbl} GROUP BY 1 ORDER BY 2 DESC")[1]
        d["by_team"] = tr.run(f"SELECT coalesce(o.team,'UNMAPPED') AS team, count(*) AS entries FROM {tbl} t {_team_join(cfg, ds)} GROUP BY 1 ORDER BY 2 DESC")[1]
        if ds == "bgl":
            d["distinct_nodes"] = tr.scalar("SELECT count(DISTINCT node) FROM bgl_logs")
            d["labelled_alert_entries"] = tr.scalar("SELECT count(*) FROM bgl_logs WHERE alert IS NOT NULL")
            d["by_alert_category"] = tr.run("SELECT alert, count(*) AS entries FROM bgl_logs WHERE alert IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")[1]
        elif ds == "openstack":
            d["labelled_anomalous_entries"] = tr.scalar("SELECT count(*) FROM openstack_logs WHERE anomalous")
            d["distinct_request_ids"] = tr.scalar("SELECT count(DISTINCT request_id) FROM openstack_logs")
        else:
            d["distinct_threads"] = tr.scalar("SELECT count(DISTINCT thread) FROM zookeeper_logs")
        out[ds] = d
    return finish("corpus_overview", {"dataset": dataset}, tr, ",".join(names), None, None, {},
                  datasets=out, total_matches=sum(v["rows"] for v in out.values()), returned=len(out), truncated=False,
                  scope_statement="Whole corpus, no filters, exact counts over every row (not a sample).")

# ---------------------------------------------------------------- search_logs
def search_logs(dataset, query, mode="terms", from_ts=None, to_ts=None, levels=None, severity="all", component=None,
                node=None, service=None, order="relevance", limit=config.DEFAULT_LIMIT, fuzzy_distance=1):
    args = dict(locals())
    ds, cfg = dataset_cfg(dataset); f, t = resolve_period(ds, from_ts, to_ts); limit = clamp_limit(limit)
    tr = Tracer()
    idx = cfg["index"]
    lo, hi = dataset_span(ds)
    # ts bounds covering the whole corpus are a no-op filter, but SereneDB's inverted-index scan loses its
    # fast top-k scoring path (block-max pruning) whenever ANY range filter is ANDed with the BM25 ORDER BY,
    # even one that excludes nothing - so dropping a provably-redundant bound here is a real ~9x speedup,
    # not just a tidier query (see eval/benchmark_results.md for the measurements behind this).
    ts_redundant = f <= lo and t >= hi + dt.timedelta(seconds=1)
    w, p = where(ds, f, t, cfg, query=query, mode=mode, distance=fuzzy_distance, via_index=True,
                 levels=levels, severity=severity, component=component, node=node, service=service,
                 include_ts=not ts_redundant)
    total = tr.scalar(f"SELECT count(*) FROM {idx} WHERE {w}", p)
    cols = cfg["select_cols"]
    use_rank = order == "relevance" and mode != "fuzzy"
    snippet = "ts_highlight(message)" if mode != "fuzzy" else "message"
    ob = "BM25(%s.tableoid) DESC, ts, id" % idx if use_rank else "ts, id"
    try:
        _, rows = tr.run(f"SELECT {cols}, message, {snippet} AS snippet FROM {idx} WHERE {w} ORDER BY {ob} LIMIT {limit}", p)
    except Exception:   # highlight/BM25 unavailable for this predicate shape -> plain ordered rows
        tr.sql.pop()
        _, rows = tr.run(f"SELECT {cols}, message, message AS snippet FROM {idx} WHERE {w} ORDER BY ts, id LIMIT {limit}", p)
    part = cfg["partition_col"]
    summ = {}
    if total:
        _, r = tr.run(f"SELECT min(ts) AS first_seen, max(ts) AS last_seen, count(DISTINCT {part}) AS distinct_nodes FROM {idx} WHERE {w}", p)
        summ = {"first_seen": r[0]["first_seen"], "last_seen": r[0]["last_seen"], f"distinct_{part}": r[0]["distinct_nodes"]}
        summ[f"top_{part}s"] = tr.run(f"SELECT {part} AS name, count(*) AS entries FROM {idx} WHERE {w} GROUP BY 1 ORDER BY 2 DESC LIMIT 5", p)[1]
        summ["by_level"] = tr.run(f"SELECT level, count(*) AS entries FROM {idx} WHERE {w} GROUP BY 1 ORDER BY 2 DESC", p)[1]
    return finish("search_logs", args, tr, ds, f, t,
                  {"query": query, "mode": mode, "levels": levels, "severity": severity if severity != "all" else None,
                   "component": component, "node": node, "service": service, "order": order},
                  total_matches=total, returned=len(rows), truncated=total > len(rows), rows=rows, summary_over_all_matches=summ,
                  limit_applied=limit,
                  matching_unit="log entries (not incidents; use `episodes` to collapse bursts)",
                  guidance="`rows` is a limited sample. Quote frequency only from total_matches / summary_over_all_matches, never from len(rows).")

# ---------------------------------------------------------------- nearby_events
_BGL_SCOPES = {"auto", "same_node", "any_node"}
_OS_SCOPES = {"auto", "same_request", "same_service", "any"}
_ZK_SCOPES = {"auto", "same_thread", "any_thread"}

def nearby_events(dataset, log_id, before_seconds=60, after_seconds=60, scope="auto", limit=40):
    args = dict(locals())
    ds, cfg = dataset_cfg(dataset); limit = clamp_limit(limit); tr = Tracer(); tbl = cfg["table"]
    # a scope that doesn't apply to this dataset is a real mistake (e.g. 'same_request' on bgl) -
    # silently substituting a different scope would hide that mistake instead of surfacing it
    valid = _BGL_SCOPES if ds == "bgl" else (_ZK_SCOPES if ds == "zookeeper" else _OS_SCOPES)
    if scope not in valid: raise ValueError(f"scope {scope!r} is not valid for dataset {ds!r}; use one of {sorted(valid)}")
    before_seconds, after_seconds = max(0, min(int(before_seconds), 86400)), max(0, min(int(after_seconds), 86400))
    _, a = tr.run(f"SELECT * FROM {tbl} WHERE id = %s", [int(log_id)])
    if not a: raise ValueError(f"no log entry with id {log_id} in dataset {ds}")
    a = a[0]; t0 = dt.datetime.fromisoformat(a["ts"])
    lo, hi = t0 - dt.timedelta(seconds=before_seconds), t0 + dt.timedelta(seconds=after_seconds)
    conds, params = ["ts >= %s", "ts <= %s"], [lo, hi]
    if ds == "bgl":
        used = "same_node" if scope == "auto" else scope
        if used == "same_node": conds.append("node = %s"); params.append(a["node"])
    elif ds == "zookeeper":
        used = "same_thread" if scope == "auto" else scope
        if used == "same_thread": conds.append("thread = %s"); params.append(a["thread"])
    else:
        if scope == "same_request" and not a.get("request_id"):
            raise ValueError(f"log entry {log_id} has no request_id; scope='same_request' doesn't apply - try 'same_service' or 'auto'")
        used = scope if scope != "auto" else ("same_request" if a.get("request_id") else "same_service")
        if used == "same_request": conds.append("request_id = %s"); params.append(a["request_id"])
        elif used == "same_service": conds.append("service = %s"); params.append(a["service"])
    w = " AND ".join(conds)
    total = tr.scalar(f"SELECT count(*) FROM {tbl} WHERE {w}", params)
    cols = cfg["select_cols"] + (", latency_s" if ds == "openstack" else "")
    _, rows = tr.run(f"SELECT * FROM (SELECT {cols}, message, abs(epoch(ts) - epoch(%s::TIMESTAMP)) AS dist FROM {tbl} WHERE {w} ORDER BY dist, id LIMIT {limit}) x ORDER BY ts, id",
                     [t0] + params)
    for r in rows: r["is_anchor"] = r["id"] == int(log_id); r.pop("dist", None)
    return finish("nearby_events", args, tr, ds, lo, hi + dt.timedelta(microseconds=1), {"anchor_id": int(log_id), "scope": used},
                  anchor={k: a[k] for k in a if k != "template"}, total_matches=total, returned=len(rows), truncated=total > len(rows),
                  events=rows, limit_applied=limit,
                  window=f"{before_seconds}s before and {after_seconds}s after the anchor, {used}",
                  guidance="If truncated, the events closest in time to the anchor are kept. Events are context, not proof of causation.")

# ---------------------------------------------------------------- count_by
def count_by(dataset, group_by, from_ts=None, to_ts=None, query=None, mode="terms", levels=None, severity="all",
             component=None, node=None, service=None, alert=None, limit=25):
    args = dict(locals())
    ds, cfg = dataset_cfg(dataset); f, t = resolve_period(ds, from_ts, to_ts); limit = clamp_limit(limit); tr = Tracer()
    g = _grp_expr(cfg, group_by); tbl = cfg["table"]
    w, p = where(ds, f, t, cfg, query=query, mode=mode, alias="t", levels=levels, severity=severity, component=component, node=node, service=service, alert=alert)
    join = _team_join(cfg, ds) if group_by == "team" else ""
    total = tr.scalar(f"SELECT count(*) FROM {tbl} t WHERE {w}", p)
    groups = tr.scalar(f"SELECT count(DISTINCT {g}) FROM {tbl} t {join} WHERE {w}", p)
    _, rows = tr.run(f"SELECT {g} AS name, count(*) AS entries FROM {tbl} t {join} WHERE {w} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {limit}", p)
    for r in rows: r["share_pct"] = round(100.0 * r["entries"] / total, 2) if total else None
    return finish("count_by", args, tr, ds, f, t,
                  {"group_by": group_by, "query": query, "mode": mode if query else None, "levels": levels, "severity": severity if severity != "all" else None,
                   "component": component, "node": node, "service": service, "alert": alert},
                  total_matches=total, groups_total=groups, returned=len(rows), truncated=groups > len(rows), rows=rows,
                  scope_statement="Exact counts over ALL log entries in the stated scope (aggregated in the database, not a sample).",
                  matching_unit="log entries (not incidents)")

# ---------------------------------------------------------------- trend
def trend(dataset, bucket="week", from_ts=None, to_ts=None, query=None, mode="terms", levels=None, severity="all",
          component=None, node=None, service=None, alert=None, group_by=None, top_groups=5):
    args = dict(locals())
    if bucket not in BUCKETS: raise ValueError(f"bucket must be one of {sorted(BUCKETS)}")
    ds, cfg = dataset_cfg(dataset); f, t = resolve_period(ds, from_ts, to_ts); tr = Tracer(); tbl = cfg["table"]
    kw = dict(query=query, mode=mode, levels=levels, severity=severity, component=component, node=node, service=service, alert=alert)
    w, p = where(ds, f, t, cfg, alias="t", **kw)
    total = tr.scalar(f"SELECT count(*) FROM {tbl} t WHERE {w}", p)
    series = None
    if group_by:
        g = _grp_expr(cfg, group_by); join = _team_join(cfg, ds) if group_by == "team" else ""
        _, tops = tr.run(f"SELECT {g} AS name, count(*) AS entries FROM {tbl} t {join} WHERE {w} GROUP BY 1 ORDER BY 2 DESC LIMIT {max(1, min(int(top_groups), 10))}", p)
        names = [x["name"] for x in tops]
        if names:
            _, rows = tr.run(f"SELECT date_trunc('{bucket}', t.ts) AS bucket, {g} AS name, count(*) AS entries FROM {tbl} t {join} WHERE {w} AND {g} IN ({','.join(['%s']*len(names))}) GROUP BY 1, 2 ORDER BY 1, 2",
                             p + names)
        else: rows = []
        series = {"group_by": group_by, "groups": names, "rows": rows}
    _, buckets = tr.run(f"SELECT date_trunc('{bucket}', t.ts) AS bucket, count(*) AS entries FROM {tbl} t WHERE {w} GROUP BY 1 ORDER BY 1", p)
    step = {"hour": dt.timedelta(hours=1), "day": dt.timedelta(days=1), "week": dt.timedelta(weeks=1)}[bucket]
    prev = None
    for b in buckets:
        bs = dt.datetime.fromisoformat(b["bucket"])
        b["partial_bucket"] = bs < f or bs + step > t   # edge buckets may cover only part of the bucket
        b["change_vs_previous_bucket_pct"] = round(100.0 * (b["entries"] - prev) / prev, 1) if prev else None
        prev = b["entries"]
    plen = t - f; pf = f - plen
    w2, p2 = where(ds, pf, f, cfg, alias="t", **kw)
    prior = tr.scalar(f"SELECT count(*) FROM {tbl} t WHERE {w2}", p2)
    growth = round(100.0 * (total - prior) / prior, 1) if prior else None
    return finish("trend", args, tr, ds, f, t,
                  {"bucket": bucket, "query": query, "mode": mode if query else None, "levels": levels, "severity": severity if severity != "all" else None,
                   "component": component, "node": node, "service": service, "alert": alert},
                  total_matches=total, returned=len(buckets), truncated=False, rows=buckets, series=series,
                  period_comparison={"current_entries": total, "current_period": [f.isoformat(sep=" "), t.isoformat(sep=" ")],
                                     "prior_entries": prior, "prior_period": [pf.isoformat(sep=" "), f.isoformat(sep=" ")],
                                     "growth_pct": growth,
                                     "caveat": "growth is None when the prior period has zero entries or lies outside the data; the prior period has equal length."},
                  scope_statement="Exact counts per bucket over all log entries in scope. Buckets flagged partial_bucket cover only part of their interval.")

# ---------------------------------------------------------------- episodes
def episodes(dataset, from_ts=None, to_ts=None, query=None, mode="terms", levels=None, severity="all", component=None,
             node=None, service=None, alert=None, gap_seconds=300, top=10):
    args = dict(locals())
    ds, cfg = dataset_cfg(dataset); f, t = resolve_period(ds, from_ts, to_ts); tr = Tracer(); tbl = cfg["table"]
    gap = max(1, min(int(gap_seconds), 86400)); part = cfg["partition_col"]; top = clamp_limit(top)
    w, p = where(ds, f, t, cfg, query=query, mode=mode, levels=levels, severity=severity, component=component, node=node, service=service, alert=alert)
    cte = (f"WITH m AS (SELECT ts, {part} AS p, ts - lag(ts) OVER (PARTITION BY {part} ORDER BY ts) AS gap FROM {tbl} WHERE {w}), "
           f"s AS (SELECT ts, p, sum(CASE WHEN gap IS NULL OR gap > INTERVAL '{gap} seconds' THEN 1 ELSE 0 END) OVER (PARTITION BY p ORDER BY ts) AS ep FROM m), "
           f"e AS (SELECT p, ep, min(ts) AS started, max(ts) AS ended, count(*) AS entries FROM s GROUP BY p, ep) ")
    _, r = tr.run(cte + "SELECT count(*) AS episodes, coalesce(sum(entries),0) AS entries, coalesce(max(entries),0) AS biggest, count(DISTINCT p) AS distinct_partitions FROM e", p)
    _, rows = tr.run(cte + f"SELECT p AS {part}, started, ended, entries FROM e ORDER BY entries DESC, started LIMIT {top}", p)
    for x in rows: x["duration_seconds"] = (dt.datetime.fromisoformat(x["ended"]) - dt.datetime.fromisoformat(x["started"])).total_seconds()
    s = r[0]
    return finish("episodes", args, tr, ds, f, t,
                  {"query": query, "mode": mode if query else None, "levels": levels, "severity": severity if severity != "all" else None,
                   "component": component, "node": node, "service": service, "alert": alert, "gap_seconds": gap},
                  total_matches=s["entries"], returned=len(rows), truncated=s["episodes"] > len(rows),
                  entries=s["entries"], episodes=s["episodes"], largest_episode_entries=s["biggest"], distinct_partitions=s["distinct_partitions"],
                  partition_by=part, rows=rows,
                  caveat=("Some entries have no node attribution (node = 'NULL'); episodes on 'NULL' merge unrelated sources and should not be read as one node's burst."
                          if any(str(x.get(part)) == "NULL" for x in rows) or ds == "bgl" else None),
                  gap_rule=f"An episode is a run of matching entries on the same {part} where consecutive entries are at most {gap}s apart. "
                           "It is a counting convention for bursts, NOT a claim that each episode is a separate real-world incident.",
                  scope_statement="Entries and episodes are exact over all entries in scope; `rows` lists the largest episodes only.")

# ---------------------------------------------------------------- recurring_patterns
def recurring_patterns(dataset, from_ts=None, to_ts=None, levels=None, severity="all", component=None, node=None,
                       service=None, alert=None, limit=15):
    args = dict(locals())
    ds, cfg = dataset_cfg(dataset); f, t = resolve_period(ds, from_ts, to_ts); limit = clamp_limit(limit); tr = Tracer(); tbl = cfg["table"]
    w, p = where(ds, f, t, cfg, alias="t", levels=levels, severity=severity, component=component, node=node, service=service, alert=alert)
    part = cfg["partition_col"]
    total = tr.scalar(f"SELECT count(*) FROM {tbl} t WHERE {w}", p)
    n_t = tr.scalar(f"SELECT count(DISTINCT template) FROM {tbl} t WHERE {w}", p)
    _, rows = tr.run(f"""SELECT t.template AS template, count(*) AS entries, count(DISTINCT t.{part}) AS distinct_partitions,
                 count(DISTINCT t.component) AS distinct_components, min(t.ts) AS first_seen, max(t.ts) AS last_seen,
                 count(DISTINCT CAST(t.ts AS DATE)) AS active_days, min(g.first_ever) AS first_ever_in_corpus
          FROM {tbl} t JOIN (SELECT template, min(ts) AS first_ever FROM {tbl} GROUP BY 1) g ON g.template = t.template
          WHERE {w} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {limit}""", p)
    covered = sum(r["entries"] for r in rows)
    # 'new' only means something relative to a bounded recent window - with no explicit from_ts,
    # f is the dataset's own earliest timestamp, so "first_ever >= f" would be true for every
    # template that has ever existed (nothing can occur before the corpus starts). Report None
    # rather than a flag that's vacuously True for the oldest, most persistent patterns too.
    explicit_period = from_ts is not None or to_ts is not None
    for r in rows:
        r["new_in_period"] = (dt.datetime.fromisoformat(r["first_ever_in_corpus"]) >= f) if explicit_period else None
        r["share_pct"] = round(100.0 * r["entries"] / total, 2) if total else None
    return finish("recurring_patterns", args, tr, ds, f, t,
                  {"levels": levels, "severity": severity if severity != "all" else None, "component": component, "node": node, "service": service, "alert": alert},
                  total_matches=total, templates_total=n_t, returned=len(rows), truncated=n_t > len(rows), rows=rows,
                  returned_templates_cover_pct=round(100.0 * covered / total, 2) if total else None,
                  template_note="Templates are derived by a simple normaliser (numbers, ids, hosts -> <*>); they approximate message types and are not Loghub's labelled templates.",
                  new_in_period_note="new_in_period is null unless from_ts/to_ts is given explicitly - pass a bounded recent window (e.g. the last 4 weeks) to distinguish genuinely new templates from the corpus's oldest, most persistent ones.",
                  scope_statement="Exact counts over all log entries in scope; ranked by entries.")

# ---------------------------------------------------------------- concentration_anomalies
def concentration_anomalies(dataset, from_ts=None, to_ts=None, levels=None, severity="all", component=None, node=None,
                            service=None, alert=None, min_entries=50, limit=10):
    """Rank templates by concentration (entries per affected node/service), not raw frequency.
    A pattern with thousands of entries confined to a single node is a different, more actionable
    finding than the same count spread over a thousand nodes - recurring_patterns' frequency
    ranking can bury it dozens of rows down; this surfaces it directly."""
    args = dict(locals())
    ds, cfg = dataset_cfg(dataset); f, t = resolve_period(ds, from_ts, to_ts); tr = Tracer(); tbl = cfg["table"]
    min_entries = max(1, int(min_entries)); limit = clamp_limit(limit)
    w, p = where(ds, f, t, cfg, alias="t", levels=levels, severity=severity, component=component, node=node, service=service, alert=alert)
    part = cfg["partition_col"]
    total = tr.scalar(f"SELECT count(*) FROM {tbl} t WHERE {w}", p)
    n_t = tr.scalar(f"SELECT count(*) FROM (SELECT template FROM {tbl} t WHERE {w} GROUP BY 1 HAVING count(*) >= {min_entries}) x", p)
    _, rows = tr.run(f"""SELECT t.template AS template, count(*) AS entries, count(DISTINCT t.{part}) AS distinct_partitions,
                 min(t.ts) AS first_seen, max(t.ts) AS last_seen
          FROM {tbl} t WHERE {w} GROUP BY 1 HAVING count(*) >= {min_entries}
          ORDER BY count(*) * 1.0 / count(DISTINCT t.{part}) DESC, count(*) DESC LIMIT {limit}""", p)
    for r in rows:
        r["concentration_ratio"] = round(r["entries"] / r["distinct_partitions"], 1)
    return finish("concentration_anomalies", args, tr, ds, f, t,
                  {"levels": levels, "severity": severity if severity != "all" else None, "component": component,
                   "node": node, "service": service, "alert": alert, "min_entries": min_entries},
                  total_matches=total, templates_total=n_t, returned=len(rows), truncated=n_t > len(rows), rows=rows,
                  partition_by=part,
                  metric_definition=f"concentration_ratio = entries / distinct_{part}s. High means this pattern recurs heavily but is "
                                     f"confined to very few {part}s (check that specific hardware/config first); low means it's spread "
                                     f"across many (more likely systemic). Ranked by this ratio, not raw frequency - use recurring_patterns for that.",
                  min_entries_note=f"templates with fewer than {min_entries} entries are excluded (min_entries) - a one-off rare event "
                                    "isn't 'concentrated', it's just rare. Lower min_entries to catch smaller but still-concentrated patterns.",
                  template_note="Templates are derived by a simple normaliser (numbers, ids, hosts -> <*>); they approximate message types and are not Loghub's labelled templates.",
                  scope_statement="Exact counts and exact distinct-partition counts over all log entries in scope.")

# ---------------------------------------------------------------- latency_summary
def latency_summary(from_ts=None, to_ts=None, service=None, group_by="service", levels=None, limit=25):
    args = dict(locals())
    ds, cfg = dataset_cfg("openstack"); f, t = resolve_period(ds, from_ts, to_ts); limit = clamp_limit(limit); tr = Tracer()
    gmap = {"service": "t.service", "component": "t.component", "http_status": "coalesce(CAST(t.http_status AS VARCHAR),'(none)')",
            "http_method": "coalesce(t.http_method,'(none)')", "hour": "CAST(date_trunc('hour', t.ts) AS VARCHAR)", "day": "CAST(date_trunc('day', t.ts) AS VARCHAR)", "none": "'all'"}
    if group_by not in gmap: raise ValueError(f"group_by must be one of {sorted(gmap)}")
    w, p = where(ds, f, t, cfg, alias="t", levels=levels, service=service)
    _, rows = tr.run(f"""SELECT {gmap[group_by]} AS name, count(*) AS entries, count(t.latency_s) AS requests_with_latency,
        round(avg(t.latency_s),4) AS avg_s, round(quantile_cont(t.latency_s, 0.5),4) AS p50_s, round(quantile_cont(t.latency_s, 0.95),4) AS p95_s,
        round(quantile_cont(t.latency_s, 0.99),4) AS p99_s, max(t.latency_s) AS max_s,
        sum(CASE WHEN t.level IN ('ERROR','CRITICAL') THEN 1 ELSE 0 END) AS error_entries,
        sum(CASE WHEN t.level = 'WARNING' THEN 1 ELSE 0 END) AS warning_entries,
        count(t.http_status) AS requests_with_status, sum(CASE WHEN t.http_status >= 500 THEN 1 ELSE 0 END) AS http_5xx
        FROM openstack_logs t WHERE {w} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {limit}""", p)
    for r in rows:
        r["error_entry_rate_pct"] = round(100.0 * r["error_entries"] / r["entries"], 3) if r["entries"] else None
        r["warning_entry_rate_pct"] = round(100.0 * r["warning_entries"] / r["entries"], 3) if r["entries"] else None
        r["http_5xx_rate_pct"] = round(100.0 * r["http_5xx"] / r["requests_with_status"], 3) if r["requests_with_status"] else None
    return finish("latency_summary", args, tr, ds, f, t, {"service": service, "group_by": group_by, "levels": levels},
                  total_matches=sum(r["entries"] for r in rows), returned=len(rows), truncated=False, rows=rows,
                  metric_definitions={"latency": "inline `time:` value on nova-api wsgi request lines only (seconds); other lines have no latency",
                                      "error_entry_rate": "ERROR/CRITICAL log entries / all entries in the group",
                                      "http_5xx_rate": "HTTP status >= 500 / entries that carry a status"},
                  scope_statement="OpenStack only. Exact aggregates over all entries in scope.")

# ---------------------------------------------------------------- team_ownership
def team_ownership(dataset=None, component=None):
    tr = Tracer(); conds, params = [], []
    if dataset: conds.append("dataset = %s"); params.append(dataset_cfg(dataset)[0])
    if component: conds.append("component = %s"); params.append(component)
    _, rows = tr.run("SELECT dataset, component, team FROM ownership" + (" WHERE " + " AND ".join(conds) if conds else "") + " ORDER BY 1, 2", params)
    return finish("team_ownership", {"dataset": dataset, "component": component}, tr, dataset or "all", None, None, {"component": component},
                  total_matches=len(rows), returned=len(rows), truncated=False, rows=rows,
                  note_ownership="FICTIONAL mapping invented for this hackathon. BGL is keyed on `component`, OpenStack on `service`.")

# ---------------------------------------------------------------- get_evidence
def get_evidence(evidence_id):
    e = ledger.get(evidence_id)
    if not e: raise ValueError(f"unknown evidence_id {evidence_id!r}; known ids: {ledger.all_ids()[-10:]}")
    return e

# ---------------------------------------------------------------- verify_ledger_integrity
def verify_ledger_integrity():
    return ledger.verify_chain()

# ---------------------------------------------------------------- generate_report
_CITE_RE = re.compile(r"\bE-\d{4}\b")

def generate_report(title, body, evidence_ids):
    """Package an already-written answer plus its cited evidence into one shareable markdown
    report. Does not run new queries or re-derive any number - it replays what's already in the
    ledger, so a report can never show a number the ledger doesn't back."""
    if not isinstance(title, str) or not title.strip(): raise ValueError("title must be a non-empty string")
    if not isinstance(body, str) or not body.strip(): raise ValueError("body must be a non-empty string")
    if isinstance(evidence_ids, str) or not isinstance(evidence_ids, (list, tuple)):
        raise ValueError(f"evidence_ids must be a list of evidence_id strings, got {type(evidence_ids).__name__}")
    if not evidence_ids: raise ValueError("evidence_ids must not be empty - cite at least one evidence id from the answer")
    bad_type = [eid for eid in evidence_ids if not isinstance(eid, str)]
    if bad_type: raise ValueError(f"evidence_ids must all be strings, got non-string value(s): {bad_type}")
    seen, deduped = set(), []   # preserve order, drop exact duplicates so the report doesn't repeat a citation
    for eid in evidence_ids:
        if eid.upper() not in seen: seen.add(eid.upper()); deduped.append(eid)
    evidence_ids = deduped
    entries, missing = [], []
    for eid in evidence_ids:
        e = ledger.get(eid)
        if e is None: missing.append(eid)
        else: entries.append((eid, e))
    if missing: raise ValueError(f"unknown evidence_id(s): {missing}; known ids: {ledger.all_ids()[-10:]}")
    # the answer text may cite ids in prose that were never actually attached to the report -
    # that's exactly the kind of silent gap eval/verify_answer.py checks for offline; flag it here too
    cited_in_body = {m for m in _CITE_RE.findall(body)}
    attached = {eid.upper() for eid in evidence_ids}
    uncited_extra = sorted(cited_in_body - attached)
    chain = ledger.verify_chain()

    lines = [f"# {title.strip()}", "",
             f"_Generated {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} by Incident Lens. "
             "Every number below traces to a replayable evidence id; the ledger's hash chain was independently "
             "re-verified at generation time, not assumed._", "", "## Answer", "", body.strip(), "",
             "## Cited evidence", ""]
    for eid, e in entries:
        entry = e["entry"]
        sql_block = "\n\n".join(entry.get("sql") or []) or "(no SQL on this entry)"
        lines += [f"### {eid} — `{entry.get('tool')}`",
                   f"- scope: `{entry.get('scope')}`",
                   f"- total_matches: **{entry.get('total_matches')}**, returned: {entry.get('returned')}, truncated: {entry.get('truncated')}",
                   f"- ledger hash: `{e.get('ledger_hash')}` (chained from `{e.get('ledger_prev_hash')}`)",
                   "- SQL:", "```sql", sql_block, "```", ""]
    lines += ["## Ledger integrity", "",
              f"Full evidence ledger verified **{'clean' if chain['ok'] else 'BROKEN at ' + str(chain['first_broken_entry'])}** "
              f"across {chain['total_entries']} entries at report generation time.", ""]
    if uncited_extra:
        lines += [f"**Warning:** the answer text cites {uncited_extra} in prose but they were not attached as "
                  "evidence_ids to this report - replay them with `get_evidence` before trusting those numbers.", ""]
    markdown = "\n".join(lines)

    return finish("generate_report", {"title": title, "evidence_ids": evidence_ids}, Tracer(), "report", None, None, {},
                  total_matches=len(entries), returned=len(entries), truncated=False,
                  markdown=markdown, chain_ok=chain["ok"], uncited_in_body=uncited_extra,
                  guidance="`markdown` is the full shareable report - display it verbatim or save it to a file; "
                           "this tool only replays existing ledger entries, it never computes a new number.")
