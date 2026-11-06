"""
Create (or re-create) the database and apply every migration in order.

Cross-platform alternative to reset_db.sh - works on Windows without bash
or the psql client. Needs: pip install "psycopg[binary]"

    python packages/db/setup_db.py                 # (re)creates release_mgmt - wipes it
    python packages/db/setup_db.py my_db_name
    python packages/db/setup_db.py --upgrade       # keep the data, apply only new migrations

Connection defaults match docker compose (postgres/postgres on localhost:5432).
Override with PG_ADMIN_URL, e.g.
    set PG_ADMIN_URL=postgresql://postgres:mypassword@localhost:5432/postgres   (Windows cmd)
    $env:PG_ADMIN_URL="postgresql://postgres:mypassword@localhost:5432/postgres" (PowerShell)
WARNING: drops the target database if it exists.
"""
import os
import sys
from pathlib import Path

try:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
except ImportError:
    sys.exit('psycopg is not installed. Run:  python -m pip install -r packages/api/requirements.txt')

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
UPGRADE = "--upgrade" in sys.argv
DB = ARGS[0] if ARGS else "release_mgmt"
ADMIN_URL = os.environ.get("PG_ADMIN_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
MIGRATIONS = sorted((Path(__file__).resolve().parent / "migrations").glob("*.sql"))


TRACK = """CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"""


def upgrade() -> None:
    """Apply migrations the database has not seen yet, keeping all data."""
    try:
        conn = psycopg.connect(make_conninfo(ADMIN_URL, dbname=DB), autocommit=True, connect_timeout=5)
    except psycopg.OperationalError as e:
        sys.exit(f"Cannot open database {DB}: {e}\nRun without --upgrade to create it.")
    with conn:
        exists = lambda rel: conn.execute("SELECT to_regclass(%s) IS NOT NULL", (rel,)).fetchone()[0]
        if not exists("schema_migrations"):
            # Databases created before migrations were tracked: work out how far they got.
            if exists("notifications"):
                baseline = 6
            elif exists("v_users"):
                baseline = 5
            else:
                sys.exit("This database is too old to upgrade in place. Run `npm run db:setup` "
                         "(without --upgrade) to recreate it.")
            conn.execute(TRACK)
            for path in MIGRATIONS[:baseline]:
                conn.execute("INSERT INTO schema_migrations(filename) VALUES (%s) ON CONFLICT DO NOTHING", (path.name,))
        done = {r[0] for r in conn.execute("SELECT filename FROM schema_migrations")}
        pending = [p for p in MIGRATIONS if p.name not in done]
        for path in pending:
            print(f"applying {path.name}")
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations(filename) VALUES (%s)", (path.name,))
    print(f"database {DB} is up to date" + ("" if pending else " (nothing to apply)"))


def main() -> None:
    if UPGRADE:
        return upgrade()
    try:
        admin = psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=5)
    except psycopg.OperationalError as e:
        sys.exit(f"Cannot connect to PostgreSQL at {ADMIN_URL}\n  {e}\n"
                 "Start it first, e.g.  docker compose up -d db  - or set PG_ADMIN_URL to your own server.")
    with admin:
        # End other sessions so the database can be dropped.
        admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                      "WHERE datname = %s AND pid <> pg_backend_pid()", (DB,))
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(DB)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DB)))

    target = make_conninfo(ADMIN_URL, dbname=DB)
    with psycopg.connect(target, autocommit=True) as conn:
        for path in MIGRATIONS:
            print(f"applying {path.name}")
            # Without parameters psycopg sends the whole file as one simple
            # query, so multi-statement scripts and $$ functions work.
            conn.execute(path.read_text(encoding="utf-8"))
        conn.execute(TRACK)
        for path in MIGRATIONS:
            conn.execute("INSERT INTO schema_migrations(filename) VALUES (%s)", (path.name,))
        n = conn.execute("SELECT count(*) FROM users").fetchone()[0]
    print(f"database {DB} ready ({n} demo users, password Passw0rd!)")
    print("The 20 demo releases are created automatically the first time the API starts (npm run dev).")


if __name__ == "__main__":
    main()
