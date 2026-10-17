"""Evidence ledger: every tool call is stored (tool, args, SQL, counts, truncation, result)
BEFORE it is returned, so any number in an answer can be replayed with get_evidence.

Tamper-evident by construction: each row's entry_hash = sha256(prev row's entry_hash + this row's
own content), so editing or deleting any stored row breaks the chain from that point on - verify_chain
re-derives every hash from scratch and does not trust anything stored in the row except its content."""
import json, hashlib, sqlite3, datetime as dt, threading
from . import config

_lock = threading.Lock()
GENESIS = "0" * 64

def _db(path=None):
    path = path or config.LEDGER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("""CREATE TABLE IF NOT EXISTS evidence (
        n INTEGER PRIMARY KEY AUTOINCREMENT, evidence_id TEXT UNIQUE, created_at TEXT, tool TEXT,
        args_json TEXT, sql_json TEXT, total_matches INTEGER, returned INTEGER, truncated INTEGER,
        result_json TEXT, prev_hash TEXT, entry_hash TEXT)""")
    cols = {r[1] for r in c.execute("PRAGMA table_info(evidence)")}
    for col in ("prev_hash", "entry_hash"):   # migration for a ledger created before chaining existed
        if col not in cols: c.execute(f"ALTER TABLE evidence ADD COLUMN {col} TEXT")
    return c

def _content_hash(prev_hash, n, eid, created_at, tool, args_json, sql_json, total_matches, returned, truncated, result_json):
    """Deterministic hash of one row's content, chained to the previous row. Excludes nothing that was
    actually stored, so a verifier with only the raw rows can redo this independently of our code."""
    payload = json.dumps([n, eid, created_at, tool, args_json, sql_json, total_matches, returned, truncated, result_json],
                         sort_keys=False, default=str)
    return hashlib.sha256((prev_hash + payload).encode()).hexdigest()

def record(tool, args, envelope):
    """Persist an envelope; returns it with evidence_id (and its ledger hash) filled in."""
    with _lock:
        c = _db()
        prev = c.execute("SELECT entry_hash FROM evidence ORDER BY n DESC LIMIT 1").fetchone()
        prev_hash = prev[0] if prev else GENESIS
        created_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        args_json = json.dumps(args, default=str)
        sql_json = json.dumps(envelope.get("sql"), default=str)
        total_matches, returned = envelope.get("total_matches"), envelope.get("returned")
        truncated = int(bool(envelope.get("truncated")))
        cur = c.execute("INSERT INTO evidence (created_at, tool, args_json, sql_json, total_matches, returned, truncated, result_json, prev_hash) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (created_at, tool, args_json, sql_json, total_matches, returned, truncated, "{}", prev_hash))
        n = cur.lastrowid; eid = f"E-{n:04d}"
        envelope = {"evidence_id": eid, **envelope}
        result_json = json.dumps(envelope, default=str)
        entry_hash = _content_hash(prev_hash, n, eid, created_at, tool, args_json, sql_json, total_matches, returned, truncated, result_json)
        c.execute("UPDATE evidence SET evidence_id=?, result_json=?, entry_hash=? WHERE n=?", (eid, result_json, entry_hash, n))
        c.commit(); c.close()
        envelope["ledger_hash"] = entry_hash
        return envelope

def get(evidence_id):
    c = _db()
    row = c.execute("SELECT created_at, tool, args_json, result_json, prev_hash, entry_hash FROM evidence WHERE evidence_id=?",
                    (evidence_id.upper(),)).fetchone()
    c.close()
    if not row: return None
    return {"created_at": row[0], "tool": row[1], "arguments": json.loads(row[2]), "entry": json.loads(row[3]),
            "ledger_hash": row[5], "ledger_prev_hash": row[4]}

def all_ids():
    c = _db(); ids = [r[0] for r in c.execute("SELECT evidence_id FROM evidence ORDER BY n")]; c.close(); return ids

def verify_chain(path=None):
    """Re-derive every row's hash from its stored content and check the chain is unbroken.
    Trusts nothing but the raw columns - if a row's content_json were hand-edited after the fact,
    or a row deleted, the recomputed hash (or the prev_hash link) stops matching from that point on.
    `path` checks a ledger file other than the live one (e.g. a tamper-demo snapshot) WITHOUT
    touching config.LEDGER_PATH - that global is read by every other request too, so swapping it
    even briefly would risk a concurrent real tool call writing its evidence into the wrong file."""
    c = _db(path)
    rows = c.execute("SELECT n, evidence_id, created_at, tool, args_json, sql_json, total_matches, returned, "
                     "truncated, result_json, prev_hash, entry_hash FROM evidence ORDER BY n").fetchall()
    c.close()
    expected_prev, broken_at, checked = GENESIS, None, 0
    for (n, eid, created_at, tool, args_json, sql_json, total_matches, returned, truncated, result_json, prev_hash, entry_hash) in rows:
        checked += 1
        recomputed = _content_hash(expected_prev, n, eid, created_at, tool, args_json, sql_json, total_matches, returned, truncated, result_json)
        if prev_hash != expected_prev or entry_hash != recomputed:
            broken_at = eid; break
        expected_prev = entry_hash
    return {"ok": broken_at is None, "entries_checked": checked, "total_entries": len(rows),
            "first_broken_entry": broken_at, "chain_head": expected_prev if broken_at is None else None,
            "method": "each entry's hash = sha256(previous entry's hash + this entry's own stored content); "
                      "recomputed here from scratch, not trusted from storage."}
