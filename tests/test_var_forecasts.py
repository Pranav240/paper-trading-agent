"""
var_forecasts persistence (app/agent/var_forecasts.py, migration 006).

The DB test runs a small real backtest (fake prices and LLMs, real
Postgres, skipping if none is reachable -- same pattern as
test_backtest.py) and checks the forecast rows and the realized-return
fill. The fake price source applies the real session-close filter, so
last_close is genuinely close(D-1) and realized_return is close(D) /
close(D-1) - 1, not something the fake made convenient.
"""

import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from app.agent.backtest import run_backtest
from app.agent.data_sources import HistoricalHeadlineSource, PriceBar, bars_closed_before
from app.agent.graph import build_decision_graph
from app.agent.state import TentativeDecision
from app.agent.var_forecasts import fill_realized_returns, position_after, record_var_forecast
from app.repository.backtest import BacktestRepository
from tests.agent_fakes import FakeLLM

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)
SYMBOL = "VARTEST"
NAME = "test var forecasts"


def test_position_after():
    assert position_after(3, "BUY", 2) == 5
    assert position_after(3, "SELL", 2) == 1
    assert position_after(3, "HOLD", 0) == 3


async def test_no_row_without_var():
    # Returns before touching the connection, so no database is needed.
    written = await record_var_forecast(
        None,
        decision_id=1, backtest_id=None, symbol="AAPL",
        as_of=datetime(2024, 1, 2, tzinfo=timezone.utc),
        risk_raw_output={"var_95": None}, final_action="HOLD", final_quantity=0,
    )
    assert written is False


class ClosedBarsSource:
    """Serves a fixed bar history, filtered exactly like AlpacaPriceSource."""

    def __init__(self, bars):
        self._bars = bars

    async def get_recent_bars(self, symbol, as_of, lookback_days=30):
        start = as_of - timedelta(days=lookback_days)
        return bars_closed_before([b for b in self._bars if b.timestamp >= start], as_of)


def _daily_bars(end: date, n: int) -> list[PriceBar]:
    """n consecutive calendar days ending `end`, stamped at midnight New
    York in winter (05:00 UTC) like Alpaca; closes wiggle so VaR > 0."""
    bars = []
    for i in range(n):
        day = end - timedelta(days=n - 1 - i)
        close = Decimal("100") + Decimal((i * 7) % 5) - Decimal("0.5") * (i % 3)
        bars.append(
            PriceBar(
                symbol=SYMBOL,
                timestamp=datetime(day.year, day.month, day.day, 5, tzinfo=timezone.utc),
                open=close, high=close, low=close, close=close, volume=1,
            )
        )
    return bars


@pytest.fixture
async def pool():
    try:
        test_pool = AsyncConnectionPool(DATABASE_URL, open=False)
        await test_pool.open(wait=True, timeout=3)
    except psycopg.OperationalError:
        pytest.skip("no reachable Postgres database — skipping DB integration test")
        return
    yield test_pool
    await test_pool.close()


async def _cleanup(pool):
    async with pool.connection() as conn:
        await conn.execute(
            "DELETE FROM decisions WHERE run_id IN (SELECT id FROM runs WHERE backtest_id IN "
            "(SELECT id FROM backtests WHERE name = %s))", (NAME,)
        )
        await conn.execute(
            "DELETE FROM runs WHERE backtest_id IN (SELECT id FROM backtests WHERE name = %s)",
            (NAME,),
        )
        await conn.execute("DELETE FROM backtests WHERE name = %s", (NAME,))


async def test_backtest_writes_forecasts_and_fills_realized_returns(pool):
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO watchlist (symbol, active) VALUES (%s, false) "
            "ON CONFLICT (symbol) DO NOTHING", (SYMBOL,)
        )
    bars = _daily_bars(date(2022, 1, 12), 400)
    source = ClosedBarsSource(bars)
    close_on = {b.timestamp.date(): b.close for b in bars}

    try:
        def make_graph(backtest_id: int):
            return build_decision_graph(
                price_source=source,
                headline_source=HistoricalHeadlineSource(pool),  # none -> HOLD path
                repository=BacktestRepository(pool, backtest_id),
                portfolio_llm=FakeLLM(
                    TentativeDecision(action="BUY", quantity=5, confidence=0.9, reasoning="t")
                ),
            )

        backtest_id = await run_backtest(
            pool, make_graph, name=NAME, symbols=[SYMBOL],
            window_start=date(2022, 1, 10), window_end=date(2022, 1, 12),
        )

        async with pool.connection() as conn:
            filled = await fill_realized_returns(conn, source, backtest_id=backtest_id)
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT as_of, var, last_close, realized_return, breach, backtest_id "
                    "FROM var_forecasts WHERE backtest_id = %s ORDER BY as_of",
                    (backtest_id,),
                )
                rows = await cur.fetchall()

        assert len(rows) == 3 and filled == 3
        for as_of, var, last_close, realized, breach, bt_id in rows:
            day = as_of.date()
            assert bt_id == backtest_id
            assert var > 0
            # Forecast saw only the prior day's close...
            assert last_close == close_on[day - timedelta(days=1)]
            # ...and is scored against that day's own close.
            expected = close_on[day] / close_on[day - timedelta(days=1)] - 1
            assert realized == pytest.approx(expected, abs=1e-6)
            assert breach == (realized < -var)
    finally:
        await _cleanup(pool)
