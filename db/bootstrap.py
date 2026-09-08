"""
Bring a database from empty to usable: migrations, then seed.

This exists for containers. Locally you run `python db/migrate.py` and
then apply `db/seed.sql` by hand (see the README), which is fine when a
human is driving. A container starting up has nobody to do the second
step, and `POST /run/trigger` fails with a foreign key violation until
`watchlist` has at least one row — so the api service runs this instead.

Deliberately NOT folded into db/migrate.py: migrations define structure,
seeding decides what data to start with. Keeping them separate is the
same reasoning that keeps db/seed.sql out of db/migrations/. This script
just calls both in the right order.

Both halves are idempotent, so running this on every container start is
safe: migrate.py skips migrations already in schema_migrations, and
seed.sql is written with ON CONFLICT DO NOTHING.

Usage:
    python db/bootstrap.py
"""

from __future__ import annotations

from pathlib import Path

import psycopg

# Plain `import migrate`, not `from db.migrate import ...`: this is run as
# `python db/bootstrap.py`, which puts db/ itself on sys.path. There's no
# package __init__ in db/ and adding one just to satisfy an import would
# be tail-wagging-dog.
from migrate import DATABASE_URL, main as run_migrations

SEED_PATH = Path(__file__).parent / "seed.sql"


def apply_seed() -> None:
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(SEED_PATH.read_text())
    print(f"seed    {SEED_PATH.name} applied")


if __name__ == "__main__":
    run_migrations()
    apply_seed()
    print("bootstrap done.")
