from fastapi import APIRouter, Depends

from app.models import Position
from app.store import InMemoryStore, get_store

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("", response_model=list[Position])
def list_positions(
    symbol: str | None = None,
    store: InMemoryStore = Depends(get_store),
) -> list[Position]:
    """List open paper-trading positions, optionally filtered by symbol.

    `response_model=list[Position]` is doing real work here: even though
    the function returns whatever `store.list_positions()` gives back,
    FastAPI validates and serializes that return value against
    `Position` before it goes on the wire. If the store ever returned
    something with a missing field, you'd get a 500 with a clear
    validation error here, not a malformed JSON blob shipped to a
    client.
    """
    return store.list_positions(symbol=symbol)
