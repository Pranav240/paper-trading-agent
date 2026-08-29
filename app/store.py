"""
In-memory stub store — the thing Phase 02 will replace.

Why this exists as its own class instead of just module-level lists used
directly in the route handlers: FastAPI's dependency injection works by
you declaring "this endpoint needs a Store" via `Depends(get_store)`, and
FastAPI calls `get_store()` for you and hands the result in. Today
`get_store()` returns this in-memory object. In Phase 02, `get_store()`
(or a sibling function with the same shape) will instead open a Postgres
session and return a repository object with the SAME method names
(`list_positions`, `list_decisions`, `record_run`). The route handlers
in app/routers/*.py won't need to change at all — only this file does.

That seam is the actual lesson of "dependency injection": it's not about
FastAPI syntax, it's about writing the caller (the route) against an
interface instead of a concrete implementation, so the implementation can
change underneath it.

Caveat, on purpose: this store is a single process-lifetime Python object.
Restart the server and every position/decision is gone. That's not a bug
to fix here — it's the exact motivation for Phase 02's real database.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.models import Action, Decision, Position, RunResult, RunStatus


class InMemoryStore:
    def __init__(self) -> None:
        self._decisions: list[Decision] = []
        self._positions: dict[str, Position] = {}
        self._next_decision_id = 1
        self._next_run_id = 1
        self._seed()

    def _seed(self) -> None:
        """Seed with one fake position + decision so the endpoints have
        something to return before any real run has happened."""
        now = datetime.now(timezone.utc)
        self._positions["AAPL"] = Position(
            symbol="AAPL",
            quantity=10,
            avg_entry_price=Decimal("190.00"),
            current_price=Decimal("193.50"),
            unrealized_pnl=Decimal("35.00"),
            opened_at=now,
        )
        self._decisions.append(
            Decision(
                id=self._next_decision_id,
                symbol="AAPL",
                action=Action.BUY,
                confidence=0.62,
                reasoning="Seed data — no real agent run has happened yet.",
                technicals_snapshot={"rsi_14": 54.2, "sma_20": 191.10},
                sentiment_snapshot={"headline_score": 0.10, "n_headlines": 3},
                created_at=now,
            )
        )
        self._next_decision_id += 1

    def list_positions(self, symbol: str | None = None) -> list[Position]:
        positions = list(self._positions.values())
        if symbol:
            positions = [p for p in positions if p.symbol == symbol.upper()]
        return positions

    def list_decisions(
        self, symbol: str | None = None, limit: int = 50
    ) -> list[Decision]:
        decisions = self._decisions
        if symbol:
            decisions = [d for d in decisions if d.symbol == symbol.upper()]
        # Most recent first — matches how you'd actually want to read a log.
        return sorted(decisions, key=lambda d: d.created_at, reverse=True)[:limit]

    def trigger_run(self) -> RunResult:
        """Fake one agent cycle.

        Phase 03 replaces this method's body with an actual LangGraph
        invocation. The route that calls this (POST /run/trigger)
        doesn't need to know or care.
        """
        started_at = datetime.now(timezone.utc)

        decision = Decision(
            id=self._next_decision_id,
            symbol="AAPL",
            action=Action.HOLD,
            confidence=0.55,
            reasoning="Stub run — Phase 03 will replace this with a real "
            "LangGraph decision flow.",
            technicals_snapshot={"rsi_14": 51.0, "sma_20": 191.40},
            sentiment_snapshot={"headline_score": 0.02, "n_headlines": 1},
            created_at=started_at,
        )
        self._decisions.append(decision)
        self._next_decision_id += 1

        result = RunResult(
            run_id=self._next_run_id,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            status=RunStatus.SUCCESS,
            decisions=[decision],
        )
        self._next_run_id += 1
        return result


# A single process-lifetime instance. This is what makes it a "singleton
# dependency" rather than a fresh empty store on every request.
_store = InMemoryStore()


def get_store() -> InMemoryStore:
    """FastAPI dependency provider.

    Route handlers never construct InMemoryStore themselves — they take
    it via `store: InMemoryStore = Depends(get_store)`. That's what lets
    tests override this exact function (see tests/conftest.py) to inject
    a clean store per test, and what lets Phase 02 swap the return value
    for a real repository without touching route code.
    """
    return _store
