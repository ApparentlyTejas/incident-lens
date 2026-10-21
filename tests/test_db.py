"""Unit tests for server/db.py's connection-management and retry logic. These exercise failure
paths that a healthy local SereneDB never actually triggers (a dead cached connection, a driver
that rejects SET statement_timeout) by stubbing db.conn / the connection object directly, rather
than skipping them as "can't happen" - they're real resilience code, just not reachable through
a happy-path test run."""
import sys, pathlib
import pytest, psycopg
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from server import db


class _FakeCursor:
    def __init__(self, rows=None, cols=None):
        self.description = [type("Col", (), {"name": c})() for c in (cols or [])] or None
        self._rows = rows or []
    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, fail_statement_timeout=False):
        self.closed = False
        self.fail_statement_timeout = fail_statement_timeout
        self.executed = []
    def execute(self, sql, params=None):
        self.executed.append(sql)
        if self.fail_statement_timeout and sql.strip().startswith("SET statement_timeout"):
            raise RuntimeError("driver does not support SET statement_timeout")
        return _FakeCursor()
    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def reset_connection_cache():
    """db._conn is module-level global state shared across tests - save/restore it so these
    tests can't leak a fake connection into any test that runs after them."""
    saved = db._conn
    db._conn = None
    yield
    db._conn = saved


def test_conn_force_new_closes_the_old_connection_and_opens_a_fresh_one(monkeypatch):
    old = _FakeConn()
    db._conn = old
    created = []
    def fake_connect(dsn, autocommit=True):
        c = _FakeConn()
        created.append(c)
        return c
    monkeypatch.setattr(psycopg, "connect", fake_connect)
    new = db.conn(force_new=True)
    assert old.closed is True
    assert new is created[0] and new is not old


def test_conn_force_new_tolerates_close_raising_on_the_old_connection(monkeypatch):
    class BadCloseConn(_FakeConn):
        def close(self):
            raise RuntimeError("already gone")
    db._conn = BadCloseConn()
    monkeypatch.setattr(psycopg, "connect", lambda dsn, autocommit=True: _FakeConn())
    # must not raise even though the stale connection's close() blew up
    new = db.conn(force_new=True)
    assert new is not None


def test_conn_tolerates_a_driver_that_rejects_statement_timeout(monkeypatch):
    # conn() best-effort sets a statement timeout and swallows any failure doing so - a
    # connection must still be usable even if that one SET command isn't supported
    monkeypatch.setattr(psycopg, "connect", lambda dsn, autocommit=True: _FakeConn(fail_statement_timeout=True))
    c = db.conn(force_new=True)
    assert c is not None and c.closed is False


def test_run_retries_once_with_a_fresh_connection_after_an_operational_error(monkeypatch):
    # a cached connection's .closed flag only reflects an explicit local close(), not a server
    # that died and restarted underneath it - that surfaces as OperationalError on first use,
    # and run() must retry exactly once with force_new=True rather than propagate it immediately
    calls = {"n": 0}
    good = _FakeConn()

    class DeadConn(_FakeConn):
        def execute(self, sql, params=None):
            raise psycopg.OperationalError("server closed the connection unexpectedly")

    def fake_conn(force_new=False):
        calls["n"] += 1
        return good if force_new else DeadConn()

    monkeypatch.setattr(db, "conn", fake_conn)
    cols, rows = db.run("select 1 as n")
    assert cols == [] and rows == []  # _FakeCursor has no description -> run() returns ([], [])
    assert calls["n"] == 2  # first call (dead conn) + the force_new retry


def test_run_coerces_count_like_columns_returned_as_text(monkeypatch):
    # SereneDB's index-backed count scans can return a count-like column as text; run() must
    # still hand tools an int (tools.py compares these directly to counts from plain SQL)
    class CountAsTextConn(_FakeConn):
        def execute(self, sql, params=None):
            return _FakeCursor(rows=[("42",)], cols=["entries"])
    monkeypatch.setattr(db, "conn", lambda force_new=False: CountAsTextConn())
    cols, rows = db.run("select count(*) as entries from bgl_logs")
    assert rows == [{"entries": 42}] and isinstance(rows[0]["entries"], int)
