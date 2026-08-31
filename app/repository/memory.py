"""
In-memory implementation of the Repository protocol.

This used to be the only implementation (Phase 01). Now that
PostgresRepository exists, this one's job has narrowed: it's used by the
test suite (tests/conftest.py) for fast, isolated tests that don't need a
real database, and it's a working reference for what "no I/O" looks like
against the exact same interface a real backend implements.

Methods are `async def` even though nothing here actually awaits
anything — that keeps this a genuine drop-in for PostgresRepository
wherever the Repository protocol is expected (see app/repository/base.py
for why that uniformity matters).
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

    async def list_positions(self, symbol: str | None = None) -> list[Position]:
        positions = list(self._positions.values())
        if symbol:
            positions = [p for p in positions if p.symbol == symbol.upper()]
        return positions

    async def list_decisions(
        self, symbol: str | None = None, limit: int = 50
    ) -> list[Decision]:
        decisions = self._decisions
        if symbol:
            decisions = [d for d in decisions if d.symbol == symbol.upper()]
        return sorted(decisions, key=lambda d: d.created_at, reverse=True)[:limit]

    async def trigger_run(self) -> RunResult:
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
