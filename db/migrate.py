"""
The entire "migration framework" for this project — on purpose.

No Alembic, no autogeneration, no ORM model diffing. Migrations are just
numbered .sql files in db/migrations/, applied in filename order. This
script's only two jobs:

1. Track which migrations have already run, in a schema_migrations table
   it manages itself, so re-running this script is a no-op for anything
   already applied (idempotent).
2. Run each new .sql file's contents inside a transaction, so a failing
   migration doesn't leave the schema half-created.

This is deliberately close to the metal: you write raw DDL, this script
just remembers what it already ran. That's the whole point of "no ORM
hiding the SQL from me" — extended to the migration tooling too, not
just the queries.

Usage:
    python db/migrate.py
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)


def ensure_migrations_table(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename    TEXT PRIMARY KEY,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def already_applied(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def main() -> None:
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        print(f"No .sql files found in {MIGRATIONS_DIR}")
        return

    with psycopg.connect(DATABASE_URL, autocommit=False) as conn:
        ensure_migrations_table(conn)
        conn.commit()

        applied = already_applied(conn)

        for path in migration_files:
            if path.name in applied:
                print(f"skip    {path.name} (already applied)")
                continue

            sql = path.read_text()
            print(f"apply   {path.name}")
            try:
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute(
                        "INSERT INTO schema_migrations (filename) VALUES (%s)",
                        (path.name,),
                    )
            except Exception:
                print(f"FAILED  {path.name} — transaction rolled back")
                raise

        print("done.")


if __name__ == "__main__":
    main()
