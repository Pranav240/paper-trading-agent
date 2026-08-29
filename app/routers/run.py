from fastapi import APIRouter, Depends, HTTPException

from app.models import RunResult
from app.store import InMemoryStore, get_store

router = APIRouter(prefix="/run", tags=["run"])


@router.post("/trigger", response_model=RunResult)
def trigger_run(store: InMemoryStore = Depends(get_store)) -> RunResult:
    """Manually trigger one agent decision cycle.

    This is POST, not GET, because it has a side effect (it appends to
    the decision log) — a core REST convention: GET should be safe to
    call repeatedly with no consequences, POST is for "do a thing."

    The try/except here is a placeholder for a real pattern: once Phase
    03 replaces `store.trigger_run()` with an actual LangGraph
    invocation that calls external APIs (market data, sentiment), THIS
    is where those failures surface, and HTTPException is how you turn
    "something broke internally" into a clean HTTP status instead of a
    stack trace leaking to the caller.
    """
    try:
        return store.trigger_run()
    except Exception as exc:  # noqa: BLE001 — placeholder until Phase 03
        raise HTTPException(status_code=500, detail=f"Run failed: {exc}") from exc
