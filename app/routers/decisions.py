from fastapi import APIRouter, Depends, Query

from app.models import Decision
from app.store import InMemoryStore, get_store

router = APIRouter(prefix="/decisions", tags=["decisions"])


@router.get("", response_model=list[Decision])
def list_decisions(
    symbol: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    store: InMemoryStore = Depends(get_store),
) -> list[Decision]:
    """The decision log, most recent first.

    `Query(default=50, ge=1, le=500)` is validation living in the
    signature instead of an `if` statement in the body — pass
    `?limit=0` or `?limit=10000` and FastAPI rejects it with a 422
    before this function runs at all.
    """
    return store.list_decisions(symbol=symbol, limit=limit)
