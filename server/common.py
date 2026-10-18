"""Shared building blocks: scope/filters -> SQL predicates, SQL rendering, envelope helper."""
import re, datetime as dt
from . import config, db, ledger

def dataset_cfg(dataset):
    d = (dataset or "").lower()
    if d not in config.DATASETS:
        raise ValueError(f"dataset must be one of {sorted(config.DATASETS)}, got {dataset!r}")
    return d, config.DATASETS[d]

def parse_ts(v, name):
    if v in (None, ""): return None
    try:
        return dt.datetime.fromisoformat(str(v).replace("T", " ").rstrip("Z"))
    except ValueError:
        raise ValueError(f"{name} must be ISO date/time like '2005-06-03' or '2005-06-03 15:42:50', got {v!r}")

_span_cache = {}
def dataset_span(dataset):
    if dataset not in _span_cache:
        cfg = config.DATASETS[dataset]
        _, r = db.run(f"SELECT min(ts) AS lo, max(ts) AS hi FROM {cfg['table']}")
        lo, hi = r[0]["lo"], r[0]["hi"]
        _span_cache[dataset] = (dt.datetime.fromisoformat(lo), dt.datetime.fromisoformat(hi))
    return _span_cache[dataset]

def resolve_period(dataset, from_ts, to_ts):
    """Absolute period [from, to). Missing bounds default to the dataset's full span (never 'last N days')."""
    lo, hi = dataset_span(dataset)
    f, t = parse_ts(from_ts, "from_ts") or lo, parse_ts(to_ts, "to_ts") or (hi + dt.timedelta(seconds=1))
    if t <= f: raise ValueError("to_ts must be after from_ts (to_ts is exclusive)")
    return f, t

def text_predicate(query, mode="terms", distance=1):
    """Return (sql, params) matching the indexed `message` column. Modes:
       terms  - every whitespace-separated term must appear (each term matched exactly, case-insensitive)
       phrase - the exact phrase, tokens adjacent and in order
       any    - at least one of the terms
       fuzzy  - every term within `distance` edits (typos), Levenshtein"""
    q = (query or "").strip()
    if not q: raise ValueError("query must not be empty")
    if "\x00" in q: raise ValueError("query must not contain a NUL (0x00) byte")
    toks = q.split()
    if mode == "phrase": return ["message @@ ts_phrase(%s)"], [q]
    if mode == "any":    return ["message @@ %s"], [q]
    if mode == "fuzzy":
        d = max(1, min(int(distance), 2))
        return [f"levenshtein_matches(message, %s, {d})" for _ in toks], [t.lower() for t in toks]
    if mode == "terms":  return ["message @@ ts_phrase(%s)" for _ in toks], toks
    raise ValueError("mode must be one of terms, phrase, any, fuzzy")

def column_filters(cfg, levels=None, severity="all", component=None, node=None, service=None, alert=None, http_status=None, prefix=""):
    """Equality/range filters on plain columns (same names in the table and the index)."""
    clauses, params = [], []
    lv = [l.upper() for l in levels] if levels else (cfg["severe_levels"] if severity == "severe" else None)
    if lv:
        clauses.append(f"{prefix}level IN ({','.join(['%s'] * len(lv))})"); params += lv
    for col, val in (("component", component), ("node", node), ("service", service), ("alert", alert)):
        if val is not None:
            if col in ("node", "alert") and cfg["table"] != "bgl_logs": raise ValueError(f"filter '{col}' only exists for dataset bgl")
            if col == "service" and cfg["table"] != "openstack_logs": raise ValueError("filter 'service' only exists for dataset openstack")
            clauses.append(f"{prefix}{col} = %s"); params.append(val)
    if http_status is not None:
        if cfg["table"] != "openstack_logs": raise ValueError("filter 'http_status' only exists for dataset openstack")
        clauses.append(f"{prefix}http_status = %s"); params.append(int(http_status))
    return clauses, params

def where(dataset, f, t, cfg, query=None, mode="terms", distance=1, via_index=False, alias="", include_ts=True, **filters):
    """Full WHERE clause. via_index=True queries the inverted index directly (needed for @@);
       otherwise a text query becomes `id IN (SELECT id FROM <index> WHERE ...)` on the base table.
       include_ts=False drops the (then-redundant) ts bounds - see search_logs's use of this for why."""
    p = f"{alias}." if alias else ""
    clauses = [f"{p}ts >= %s", f"{p}ts < %s"] if include_ts else []
    params = [f, t] if include_ts else []
    c, pr = column_filters(cfg, prefix=p, **filters); clauses += c; params += pr
    if query:
        tc, tp = text_predicate(query, mode, distance)
        if via_index: clauses += tc; params += tp
        else:
            clauses.append(f"{p}id IN (SELECT id FROM {cfg['index']} WHERE " + " AND ".join(tc) + ")"); params += tp
    return " AND ".join(clauses) if clauses else "TRUE", params

def render(sql, params):
    """Human-runnable SQL with parameters inlined (display only; execution is always parameterised)."""
    it = iter(params)
    def lit(v):
        if isinstance(v, (int, float)): return str(v)
        if isinstance(v, dt.datetime): return f"TIMESTAMP '{v.isoformat(sep=' ')}'"
        return "'" + str(v).replace("'", "''") + "'"
    return re.sub(r"\s+", " ", re.sub(r"%s", lambda m: lit(next(it)), sql)).strip()

class Tracer:
    """Collects every executed statement so the ledger shows exactly what ran."""
    def __init__(self): self.sql = []
    def run(self, sql, params=None):
        params = list(params or []); self.sql.append(render(sql, params)); return db.run(sql, params)
    def scalar(self, sql, params=None):
        params = list(params or []); self.sql.append(render(sql, params)); return db.scalar(sql, params)

def clamp_limit(limit):
    return max(1, min(int(limit or config.DEFAULT_LIMIT), config.HARD_LIMIT))

def finish(tool, args, tr, dataset, f, t, filters, **fields):
    env = {
        "tool": tool,
        "scope": {"dataset": dataset, "from": f.isoformat(sep=" ") if f else None, "to_exclusive": t.isoformat(sep=" ") if t else None,
                  "timezone": "log timestamps as recorded (no timezone conversion)"},
        "filters": {k: v for k, v in filters.items() if v not in (None, [], "")},
        "sql": tr.sql, **fields,
        "untrusted_fields": ["rows[].message", "rows[].snippet", "events[].message"],
        "note": config.UNTRUSTED_NOTE,
    }
    return ledger.record(tool, args, env)
