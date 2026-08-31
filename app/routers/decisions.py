from fastapi import APIRouter, Depends, Query

from app.models import Decision
from app.repository.base import Repository
from app.store import get_store

router = APIRouter(prefix="/decisions", tags=["decisions"])


@router.get("", response_model=list[Decision])
async def list_decisions(
    symbol: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    store: Repository = Depends(get_store),
) -> list[Decision]:
    return await store.list_decisions(symbol=symbol, limit=limit)
