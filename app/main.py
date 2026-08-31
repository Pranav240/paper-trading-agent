"""
FastAPI application entrypoint.

Run it with:
    uvicorn app.main:app --reload

Then open http://127.0.0.1:8000/docs.

New in Phase 02: the `lifespan` context manager. FastAPI calls the code
before `yield` once, at startup, and the code after `yield` once, at
shutdown. We use it to open the database connection pool when the app
starts and close it cleanly when the app stops — the pool shouldn't be
opened at import time (see app/db.py for why), and it shouldn't leak
connections when the process exits either.

`SKIP_DB_STARTUP` exists for tests: lifespan runs on every TestClient(),
regardless of whether a test overrides get_store() to use the in-memory
store instead of Postgres. Without this escape hatch, tests that never
touch the database would still be forced to open one — and worse, a
fresh pool object is needed per lifespan call (see create_pool()'s
docstring), so this also keeps test setup honest about what it actually
needs.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import create_pool
from app.routers import decisions, health, positions, run


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("SKIP_DB_STARTUP") == "1":
        app.state.pool = None
    else:
        app.state.pool = create_pool()
        await app.state.pool.open()

    yield

    if app.state.pool is not None:
        await app.state.pool.close()


app = FastAPI(
    title="Paper Trading Agent — Control API",
    description=(
        "V1 control plane: view positions and decisions, trigger an "
        "agent run manually. Backed by Postgres since Phase 02."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(positions.router)
app.include_router(decisions.router)
app.include_router(run.router)
