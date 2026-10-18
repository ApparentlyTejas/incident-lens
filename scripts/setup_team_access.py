#!/usr/bin/env python3
"""Provision one database role + one filtered view per team, from the (fictional) ownership
table - so a team's own connection can see only their own component's/service's log rows,
enforced by SereneDB's grant system, not application code.

SereneDB does NOT implement Postgres's native ROW LEVEL SECURITY / CREATE POLICY (verified
directly: `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` and `CREATE POLICY` both fail with
"Pragma Function ... does not exist"). This reaches the same real guarantee a different way,
with primitives SereneDB does support: a per-team VIEW pre-filtered by the ownership table, a
role with its base-table grants revoked, and SELECT granted only on that one view. Verified
end-to-end by connecting AS the restricted role (not as admin) and confirming the base tables
are denied and the view returns only that team's own rows - see tests/test_team_access.py.

Usage:
  python scripts/setup_team_access.py              # provision every team found in `ownership`
  python scripts/setup_team_access.py --drop       # remove every role/view this script creates
  python scripts/setup_team_access.py --dsn '...'  # override SERENEDB_ADMIN_DSN
"""
import argparse, os, re, secrets, sys, pathlib
import psycopg
from psycopg import sql

ROOT = pathlib.Path(__file__).resolve().parent.parent
DSN = os.environ.get("SERENEDB_ADMIN_DSN", "host=127.0.0.1 port=7890 user=postgres dbname=postgres")
TABLE = {"bgl": "bgl_logs", "openstack": "openstack_logs", "zookeeper": "zookeeper_logs"}
KEY_COL = {"bgl": "component", "openstack": "service", "zookeeper": "component"}

def slug(team):
    return re.sub(r"[^a-z0-9]+", "_", team.lower()).strip("_")

def provision(conn, dataset, team):
    s = slug(team)
    view, role = f"team_view_{dataset}_{s}", f"il_team_{dataset}_{s}"
    tbl, key_col = sql.Identifier(TABLE[dataset]), sql.Identifier(KEY_COL[dataset])
    view_id, role_id = sql.Identifier(view), sql.Identifier(role)

    # drop the view BEFORE the role: the grant lives on the view, and SereneDB (like Postgres)
    # refuses to drop a role with dependent objects still attached to it
    conn.execute(sql.SQL("DROP VIEW IF EXISTS {}").format(view_id))
    conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(role_id))

    password = secrets.token_urlsafe(18)
    conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(role_id, sql.Literal(password)))
    conn.execute(sql.SQL("""
        CREATE VIEW {view} AS
        SELECT t.* FROM {tbl} t
        JOIN ownership o ON o.dataset = {dataset} AND o.component = t.{key_col}
        WHERE o.team = {team}
    """).format(view=view_id, tbl=tbl, key_col=key_col, dataset=sql.Literal(dataset), team=sql.Literal(team)))
    conn.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(view_id, role_id))
    return role, view, password

def drop(conn, dataset, team):
    s = slug(team)
    conn.execute(sql.SQL("DROP VIEW IF EXISTS {}").format(sql.Identifier(f"team_view_{dataset}_{s}")))
    conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(f"il_team_{dataset}_{s}")))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("--drop", action="store_true", help="remove every provisioned role/view, then exit")
    a = ap.parse_args()
    conn = psycopg.connect(a.dsn, autocommit=True)
    teams = conn.execute("SELECT DISTINCT dataset, team FROM ownership ORDER BY 1, 2").fetchall()
    if not teams:
        sys.exit("ownership table is empty - run scripts/load.py first")

    if a.drop:
        for dataset, team in teams: drop(conn, dataset, team)
        print(f"dropped {len(teams)} team role(s)/view(s)")
        return

    print(f"{'role':<34} {'view':<30} team")
    creds = []
    for dataset, team in teams:
        role, view, password = provision(conn, dataset, team)
        creds.append((role, password))
        print(f"{role:<34} {view:<30} {team}")
    print(f"\nprovisioned {len(teams)} team role(s). Example connection (password generated fresh each run, not reusable):")
    r, p = creds[0]
    print(f"  psql \"host=... port=7890 user={r} password={p} dbname=postgres\"")
    print("Each role can SELECT only its own team_view_* view; the base tables are not granted to any of them.")

if __name__ == "__main__":
    main()
