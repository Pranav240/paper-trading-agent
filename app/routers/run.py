from fastapi import APIRouter, Depends, HTTPException

from app.models import RunResult
from app.repository.base import Repository
from app.store import get_store

router = APIRouter(prefix="/run", tags=["run"])


@router.post("/trigger", response_model=RunResult)
async def trigger_run(store: Repository = Depends(get_store)) -> RunResult:
    try:
        return await store.trigger_run()
    except Exception as exc:  # noqa: BLE001 — placeholder until Phase 03
        raise HTTPException(status_code=500, detail=f"Run failed: {exc}") from exc
