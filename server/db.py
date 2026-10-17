"""Thin read-only SereneDB access layer. All SQL in this project goes through run()."""
import datetime as dt, decimal, psycopg
from . import config

_conn = None

def conn():
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg.connect(config.DSN, autocommit=True)
        try:
            _conn.execute(f"SET statement_timeout = '{config.STATEMENT_TIMEOUT}'")
        except Exception:
            pass
    return _conn

def _clean(v):
    if isinstance(v, (dt.datetime, dt.date)): return v.isoformat(sep=" ") if isinstance(v, dt.datetime) else v.isoformat()
    if isinstance(v, dt.timedelta): return v.total_seconds()
    if isinstance(v, decimal.Decimal): return float(v)
    return v

COUNT_COLS = {"n", "entries", "episodes", "total", "matches", "requests", "rows", "groups", "distinct_nodes",
              "distinct_components", "distinct_partitions", "active_days", "biggest", "error_entries",
              "warning_entries", "http_5xx", "requests_with_latency", "requests_with_status"}

def run(sql, params=None):
    """Execute one parameterised statement -> (columns, rows[list of dict]).
    Count-like columns are coerced to int (index scans can return counts as text)."""
    cur = conn().execute(sql, list(params) if params else None)
    if not cur.description: return [], []
    cols = [d.name for d in cur.description]
    def fix(c, v):
        v = _clean(v)
        if c in COUNT_COLS and isinstance(v, str) and v.lstrip("-").isdigit(): return int(v)
        if c in COUNT_COLS and isinstance(v, float) and v.is_integer(): return int(v)
        return v
    return cols, [{c: fix(c, v) for c, v in zip(cols, r)} for r in cur.fetchall()]

def scalar(sql, params=None):
    _, rows = run(sql, params)
    if not rows: return None
    v = next(iter(rows[0].values()))
    return int(v) if isinstance(v, str) and v.lstrip("-").isdigit() else v
