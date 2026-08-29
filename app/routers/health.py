"""
Liveness check. Boring on purpose.

This looks trivial today, but it's the endpoint Phase 06's scheduled AWS
run and Phase 11's crash/staleness alerting will both poll: "is the
process up at all" has to be answered before "is it doing the right
thing" is even a meaningful question.
"""

from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}
