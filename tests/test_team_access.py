"""Verifies scripts/setup_team_access.py's per-team database access actually holds - by
connecting AS a restricted role (never as admin) and checking what it can and can't see.
Run after `python scripts/setup_team_access.py` against a loaded database.

SereneDB has no native ROW LEVEL SECURITY / CREATE POLICY (see that script's docstring for the
direct proof); this is the equivalent guarantee built from views + revoked base-table grants,
and this file is what proves the guarantee actually holds end to end, not just that the SQL ran."""
import os, re, secrets, sys, pathlib
import pytest, psycopg
from psycopg import sql

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
ADMIN_DSN = os.environ.get("SERENEDB_ADMIN_DSN", "host=127.0.0.1 port=7890 user=postgres dbname=postgres")

def admin_conn():
    return psycopg.connect(ADMIN_DSN, autocommit=True)

def role_dsn(role, password):
    # swap the user/password on the admin DSN rather than hand-building a new one, so this
    # still points at the right host whatever ADMIN_DSN's connection details are
    base = re.sub(r"\buser=\S+", f"user={role}", ADMIN_DSN)
    base = re.sub(r"\bpassword=\S+", "", base).strip()
    return f"{base} password={password}"

@pytest.fixture(scope="module")
def provisioned():
    """Provision one fresh, temporary role/view pair directly (not via the script's CLI, so this
    test doesn't depend on team names that may change) and clean it up after."""
    conn = admin_conn()
    password = secrets.token_urlsafe(18)
    conn.execute("DROP VIEW IF EXISTS _test_team_view")
    conn.execute("DROP ROLE IF EXISTS _test_team_role")
    conn.execute(sql.SQL("CREATE ROLE _test_team_role LOGIN PASSWORD {}").format(sql.Literal(password)))
    conn.execute("""CREATE VIEW _test_team_view AS
        SELECT t.* FROM bgl_logs t
        JOIN ownership o ON o.dataset = 'bgl' AND o.component = t.component
        WHERE o.team = 'Application Support'""")
    conn.execute("GRANT SELECT ON _test_team_view TO _test_team_role")
    yield password
    conn.execute("DROP VIEW IF EXISTS _test_team_view")
    conn.execute("DROP ROLE IF EXISTS _test_team_role")

def test_restricted_role_is_denied_the_base_tables(provisioned):
    conn = psycopg.connect(role_dsn("_test_team_role", provisioned), autocommit=True)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT count(*) FROM bgl_logs")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT count(*) FROM openstack_logs")

def test_restricted_role_sees_only_its_own_team_through_the_view(provisioned):
    admin, restricted = admin_conn(), psycopg.connect(role_dsn("_test_team_role", provisioned), autocommit=True)
    expected = admin.execute("SELECT count(*) FROM bgl_logs WHERE component = 'APP'").fetchone()[0]
    assert expected > 0
    seen = restricted.execute("SELECT count(*) FROM _test_team_view").fetchone()[0]
    assert seen == expected
    components = restricted.execute("SELECT DISTINCT component FROM _test_team_view").fetchall()
    assert components == [("APP",)]   # never any other team's component, not even one stray row

def test_setup_script_provisions_every_real_team_and_each_is_actually_isolated():
    """The real script (not the hand-built fixture above), run against the real ownership table -
    checks that a sample of the real provisioned roles each see exactly their own team's rows and
    nothing else, end to end."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("setup_team_access", ROOT / "scripts" / "setup_team_access.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    admin = admin_conn()
    teams = admin.execute("SELECT DISTINCT dataset, team FROM ownership ORDER BY 1, 2").fetchall()
    assert teams, "ownership table is empty - run scripts/load.py first"
    provisioned = {}
    try:
        for dataset, team in teams:
            role, view, password = mod.provision(admin, dataset, team)
            provisioned[(dataset, team)] = (role, view, password)

        for dataset, team in teams:
            role, view, password = provisioned[(dataset, team)]
            key_col = "component" if dataset == "bgl" else "service"
            table = "bgl_logs" if dataset == "bgl" else "openstack_logs"
            expected = admin.execute(
                f"SELECT count(*) FROM {table} t JOIN ownership o ON o.dataset=%s AND o.component=t.{key_col} WHERE o.team=%s",
                [dataset, team]).fetchone()[0]

            # which key_col values belong to this team, established independently by the admin -
            # the restricted role has no grant on `ownership` itself (views run with the
            # DEFINER's privileges for what they reference inside, not the querying role's, so
            # the view works without that grant, but a live join as the restricted role would not)
            owned_keys = {r[0] for r in admin.execute(
                "SELECT component FROM ownership WHERE dataset=%s AND team=%s", [dataset, team]).fetchall()}

            restricted = psycopg.connect(role_dsn(role, password), autocommit=True)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                restricted.execute(f"SELECT count(*) FROM {table}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                restricted.execute("SELECT count(*) FROM ownership")
            seen = restricted.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
            assert seen == expected, f"{role} saw {seen} rows, expected exactly {expected} for team {team!r}"

            seen_keys = {r[0] for r in restricted.execute(f"SELECT DISTINCT {key_col} FROM {view}").fetchall()}
            assert seen_keys and seen_keys <= owned_keys   # never a component/service outside this team's own
    finally:
        for dataset, team in teams:
            mod.drop(admin, dataset, team)
