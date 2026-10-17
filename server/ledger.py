"""Evidence ledger: every tool call is stored (tool, args, SQL, counts, truncation, result)
BEFORE it is returned, so any number in an answer can be replayed with get_evidence."""
import json, sqlite3, datetime as dt, threading
from . import config

_lock = threading.Lock()

def _db():
    config.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.LEDGER_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS evidence (
        n INTEGER PRIMARY KEY AUTOINCREMENT, evidence_id TEXT UNIQUE, created_at TEXT, tool TEXT,
        args_json TEXT, sql_json TEXT, total_matches INTEGER, returned INTEGER, truncated INTEGER,
        result_json TEXT)""")
    return c

def record(tool, args, envelope):
    """Persist an envelope; returns it with evidence_id filled in."""
    with _lock:
        c = _db()
        cur = c.execute("INSERT INTO evidence (created_at, tool, args_json, sql_json, total_matches, returned, truncated, result_json) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), tool,
                         json.dumps(args, default=str), json.dumps(envelope.get("sql"), default=str),
                         envelope.get("total_matches"), envelope.get("returned"),
                         int(bool(envelope.get("truncated"))), "{}"))
        eid = f"E-{cur.lastrowid:04d}"
        envelope = {"evidence_id": eid, **envelope}
        c.execute("UPDATE evidence SET evidence_id=?, result_json=? WHERE n=?",
                  (eid, json.dumps(envelope, default=str), cur.lastrowid))
        c.commit(); c.close()
        return envelope

def get(evidence_id):
    c = _db()
    row = c.execute("SELECT created_at, tool, args_json, result_json FROM evidence WHERE evidence_id=?", (evidence_id.upper(),)).fetchone()
    c.close()
    if not row: return None
    return {"created_at": row[0], "tool": row[1], "arguments": json.loads(row[2]), "entry": json.loads(row[3])}

def all_ids():
    c = _db(); ids = [r[0] for r in c.execute("SELECT evidence_id FROM evidence ORDER BY n")]; c.close(); return ids
