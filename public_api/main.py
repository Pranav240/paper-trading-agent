"""
Read-only API over the public snapshot (docs/data). GET routes only.

It mirrors the five MCP tools (list_backtests, list_decisions, get_decision,
explain_decision, get_trace) plus the eval, over JSON that was exported
once by scripts/export_public_snapshot.py. There is no database, no model
client and no way to start a run; a request can only read a committed file.

Run locally:
    uvicorn public_api.main:app --port 8000
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query

# In the image the snapshot is copied to /app/data; in the repo it is docs/data.
DATA = Path(os.environ.get("SNAPSHOT_DIR") or (Path(__file__).resolve().parent.parent / "docs" / "data"))
if not DATA.is_dir():
    DATA = Path(__file__).resolve().parent / "data"

MAX_LIMIT = 200

app = FastAPI(
    title="Paper Trading Agent: public snapshot API",
    description="Read-only. A static snapshot of recorded paper-trading decisions; no live model, no writes.",
    version="1.0.0",
)


@lru_cache(maxsize=1024)
def _load(name: str) -> Any:
    path = (DATA / name).resolve()
    if DATA.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="not in the snapshot")
    return json.loads(path.read_text(encoding="utf-8"))


def _decision(decision_id: int) -> dict:
    try:
        return _load(f"decisions/{decision_id}.json")
    except HTTPException:
        raise HTTPException(status_code=404, detail=f"There is no decision {decision_id} in the snapshot.")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "snapshot": _load("manifest.json")["generated"]}


@app.get("/backtests")
def list_backtests() -> dict:
    return _load("backtests.json")


@app.get("/decisions")
def list_decisions(
    backtest_id: int | None = None,
    action: str | None = Query(None, pattern="^(BUY|SELL|HOLD)$"),
    var_effect: str | None = Query(None, pattern="^(scaled|blocked)$"),
    has_explanation: bool | None = None,
    limit: int = Query(20, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> dict:
    rows = _load("decisions.json")["decisions"]
    if backtest_id is not None:
        rows = [r for r in rows if r["backtest_id"] == backtest_id]
    if action:
        rows = [r for r in rows if r["action"] == action]
    if var_effect:
        rows = [r for r in rows if r["var_effect"] == var_effect]
    if has_explanation is not None:
        rows = [r for r in rows if r["has_explanation"] == has_explanation]
    return {"decisions": rows[offset:offset + limit], "count": len(rows), "offset": offset}


@app.get("/decisions/{decision_id}")
def get_decision(decision_id: int) -> dict:
    d = _decision(decision_id)
    return {k: v for k, v in d.items() if k not in ("explanation", "trace")}


@app.get("/decisions/{decision_id}/explanation")
def explain_decision(decision_id: int) -> dict:
    e = _decision(decision_id)["explanation"]
    if e is None:
        raise HTTPException(status_code=404, detail=f"Decision {decision_id} has no stored explanation.")
    return e


@app.get("/decisions/{decision_id}/trace")
def get_trace(decision_id: int) -> dict:
    t = _decision(decision_id)["trace"]
    if t is None:
        raise HTTPException(status_code=404, detail=f"Decision {decision_id} has no stored trace.")
    return t


@app.get("/eval")
def get_eval() -> dict:
    return _load("eval.json")
