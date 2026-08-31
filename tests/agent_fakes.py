"""
Shared test doubles for the Phase 03 agent nodes.

These exist so every agent-node test can run with no network access at
all — no Alpaca keys, no OpenAI key, no Postgres. Each fake implements
just enough of the relevant Protocol's shape to satisfy the node under
test; that's the entire point of using Protocols (app/agent/data_sources.py,
app/repository/base.py) instead of concrete classes as the node
dependencies.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.models import Decision, Position, RunResult


class FakePriceSource:
    """Returns whatever bars you hand it, ignoring symbol/as_of/lookback —
    the node under test doesn't care where bars come from, just what's in
    them."""

    def __init__(self, bars):
        self._bars = bars

    async def get_recent_bars(self, symbol, as_of, lookback_days=30):
        return self._bars


class FakeHeadlineSource:
    def __init__(self, headlines):
        self._headlines = headlines

    async def get_recent_headlines(self, symbol, as_of, lookback_days=3):
        return self._headlines


class FakeRepository:
    """Only list_positions is exercised by risk_manager today, but all
    three Protocol methods are implemented so this can stand in anywhere
    a Repository is expected."""

    def __init__(self, positions: list[Position] | None = None):
        self._positions = positions or []

    async def list_positions(self, symbol: str | None = None) -> list[Position]:
        if symbol is None:
            return list(self._positions)
        return [p for p in self._positions if p.symbol == symbol]

    async def list_decisions(
        self, symbol: str | None = None, limit: int = 50
    ) -> list[Decision]:
        return []

    async def trigger_run(self) -> RunResult:
        raise NotImplementedError("not needed by these tests")


def make_position(
    symbol: str,
    quantity: int,
    avg_entry_price: str = "100.00",
    current_price: str = "100.00",
) -> Position:
    return Position(
        symbol=symbol,
        quantity=quantity,
        avg_entry_price=Decimal(avg_entry_price),
        current_price=Decimal(current_price),
        unrealized_pnl=Decimal("0"),
        opened_at=datetime(2026, 1, 1),
    )


class FakeStructuredModel:
    """Stands in for `ChatOpenAI(...).with_structured_output(Schema)`.
    `.ainvoke(messages)` just returns whatever Pydantic instance the test
    configured — no API call, no network."""

    def __init__(self, response):
        self._response = response

    async def ainvoke(self, messages):
        return self._response


class FakeLLM:
    """Stands in for `ChatOpenAI` itself. `.with_structured_output(...)`
    ignores the schema argument and returns a FakeStructuredModel that
    always answers with the response given at construction time."""

    def __init__(self, response):
        self._response = response

    def with_structured_output(self, schema):
        return FakeStructuredModel(self._response)
