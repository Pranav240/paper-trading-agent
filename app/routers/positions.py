from fastapi import APIRouter, Depends

from app.models import Position
from app.repository.base import Repository
from app.store import get_store

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("", response_model=list[Position])
async def list_positions(
    symbol: str | None = None,
    store: Repository = Depends(get_store),
) -> list[Position]:
    """List open paper-trading positions, optionally filtered by symbol.

    `async def` now (it wasn't in Phase 01) because `store.list_positions`
    does real network I/O against Postgres. FastAPI runs this on the
    event loop instead of a worker thread, so while this request is
    waiting on the database, other requests can still be handled.
    """
    return await store.list_positions(symbol=symbol)
