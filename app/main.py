"""
FastAPI application entrypoint.

Run it with:
    uvicorn app.main:app --reload

Then open http://127.0.0.1:8000/docs — that page is generated entirely
from the type hints and Pydantic models in this codebase, not hand
written. That's the payoff for being disciplined about response_model
and typed query params in the routers.
"""

from fastapi import FastAPI

from app.routers import decisions, health, positions, run

app = FastAPI(
    title="Paper Trading Agent — Control API",
    description=(
        "V1 control plane: view positions and decisions, trigger an "
        "agent run manually. Backed by an in-memory stub store until "
        "Phase 02 adds Postgres."
    ),
    version="0.1.0",
)

app.include_router(health.router)
app.include_router(positions.router)
app.include_router(decisions.router)
app.include_router(run.router)
