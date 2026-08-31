"""
The FastAPI dependency provider — Phase 01's get_store() seam, now
pointed at Postgres instead of an in-memory stub.

Note the `Request` parameter: the connection pool lives on
`app.state.pool` (set up in app/main.py's lifespan), not as a module-level
import, precisely so tests can run the app with no pool at all
(SKIP_DB_STARTUP=1) without this file needing to know that's happening.
This function builds a fresh, cheap PostgresRepository wrapper around
whatever pool the app currently has — it's not the pool itself that's
per-request, just this thin wrapper object.

This is the entire diff at the wiring layer that Phase 02 needed: routers
still call `Depends(get_store)`, still get back something satisfying the
Repository protocol, and still don't know or care what's behind it.
"""

from __future__ import annotations

from fastapi import Request

from app.repository.base import Repository
from app.repository.postgres import PostgresRepository


def get_store(request: Request) -> Repository:
    return PostgresRepository(request.app.state.pool)
