"""
The database connection pool.

Why a pool instead of one connection: FastAPI can handle many requests
"concurrently" (interleaved on one event loop, via async), and a single
database connection can only run one query at a time. Without a pool,
concurrent requests would queue up behind each other at the database
layer even though the web layer is happy to run them concurrently. A
pool hands each request its own connection for the duration of its
query, borrowed from a small set kept open and reused.

`open=False` at construction is deliberate: importing this module (which
happens the moment anything imports app.main, including test collection)
should NOT silently try to open a network connection to a database. The
pool is opened explicitly during FastAPI's lifespan startup instead — see
app/main.py. This is the same "don't do I/O at import time" discipline
that makes code testable.
"""

from __future__ import annotations

import os

from psycopg_pool import AsyncConnectionPool

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)


def create_pool() -> AsyncConnectionPool:
    """A factory, not a singleton.

    Each call to FastAPI's lifespan (once per running app instance —
    normally once per process, but once per TestClient() in tests) needs
    its OWN pool object. psycopg_pool refuses to reopen a pool that was
    already closed, so a single module-level pool shared across multiple
    lifespan cycles (as happens when several tests each spin up their own
    TestClient) breaks on the second one. A factory function sidesteps
    that: every lifespan call gets a fresh pool.
    """
    return AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=5, open=False)
