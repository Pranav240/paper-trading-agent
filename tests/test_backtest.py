"""
Integration test for the backtest runner (app/agent/backtest.py).

Like tests/test_historical_headline_source.py, this talks to a real
Postgres database (skipping itself if none is reachable) rather than
faking the DB layer — the whole point of this runner is a multi-table
transactional write pattern (backtests -> runs -> decisions ->
agent_opinions -> backtest_outcomes) plus the correctness property that
matters most here: NOTHING it does may touch the live `outcomes` /
`price_snapshots` tables. A fake pool can't catch a bug in either of
those, only a real database can.

Price and sentiment data are still faked (FakePriceSource, FakeLLM) —
this test exists to prove the persistence/orchestration is correct, not
to re-test indicator math or LLM prompting, which already have their own
unit tests against fakes.
"""

import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from app.agent.backtest import compute_backtest_metrics, run_backtest
from app.agent.data_sources import Headline, HistoricalHeadlineSource, PriceBar
from app.agent.graph import build_decision_graph
from app.agent.state import TentativeDecision
from app.repository.backtest import BacktestRepository
from tests.agent_fakes import FakeLLM, FakePriceSource

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)


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


def _rising_bars(symbol: str, n: int = 40) -> list[PriceBar]:
    """Enough bars, with enough of a trend, that RSI-14/SMA-20 are always
    computable (n > 21) and land the Technical Analyst on a real
    BUY/HOLD/SELL call instead of the "not enough history" short-circuit
    — a HOLD-only backtest wouldn't exercise backtest_outcomes at all."""
    base = datetime(2022, 1, 1, tzinfo=timezone.utc)
    return [
        PriceBar(
            symbol=symbol,
            timestamp=base + timedelta(days=i),
            open=Decimal("100") + i,
            high=Decimal("101") + i,
            low=Decimal("99") + i,
            close=Decimal("100") + i,
            volume=1_000_000,
        )
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_run_backtest_persists_without_touching_live_tables(pool):
    symbol = "BKTEST"  # not on the seeded live watchlist, deliberately

    # decisions.symbol has a FK into watchlist (001_init.sql) — a
    # backtest still needs the symbol registered there even though it's
    # inactive, exactly the tension this module's migration comment
    # flags. Insert it directly rather than going through the live
    # /watchlist API, and clean it up after.
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO watchlist (symbol, active) VALUES (%s, false) "
            "ON CONFLICT (symbol) DO NOTHING",
            (symbol,),
        )
        await conn.execute(
            "DELETE FROM historical_headlines WHERE symbol = %s", (symbol,)
        )
        await conn.execute(
            """
            INSERT INTO historical_headlines (symbol, published_at, headline, source)
            VALUES (%s, %s, 'a test headline', 'fnspid')
            """,
            # Inside the default 3-day lookback of every simulated day in
            # the Jan 10-12 window below, so this exercises the real
            # "headlines found -> call the sentiment LLM" path rather
            # than the zero-headlines short-circuit.
            (symbol, datetime(2022, 1, 9, 12, tzinfo=timezone.utc)),
        )

    try:
        from app.agent.sentiment_analyst import SentimentCall

        price_source = FakePriceSource(_rising_bars(symbol))
        headline_source = HistoricalHeadlineSource(pool)
        sentiment_llm = FakeLLM(
            SentimentCall(opinion="HOLD", confidence=0.5, reasoning="test fixture")
        )
        portfolio_llm = FakeLLM(
            TentativeDecision(
                action="BUY", quantity=5, confidence=0.7, reasoning="test fixture"
            )
        )

        def make_graph(backtest_id: int):
            return build_decision_graph(
                price_source=price_source,
                headline_source=headline_source,
                repository=BacktestRepository(pool, backtest_id),
                sentiment_llm=sentiment_llm,
                portfolio_llm=portfolio_llm,
            )

        window_start = date(2022, 1, 10)
        window_end = date(2022, 1, 12)  # 3 calendar days, all weekdays

        # Snapshot live tables before, to prove the backtest didn't touch them.
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT COUNT(*) FROM outcomes")
                outcomes_before = (await cur.fetchone())[0]
                await cur.execute("SELECT COUNT(*) FROM price_snapshots")
                snapshots_before = (await cur.fetchone())[0]

        backtest_id = await run_backtest(
            pool,
            make_graph,
            name="test backtest",
            symbols=[symbol],
            window_start=window_start,
            window_end=window_end,
        )

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT status, window_start, window_end FROM backtests WHERE id = %s",
                    (backtest_id,),
                )
                bt_row = await cur.fetchone()

                await cur.execute(
                    "SELECT COUNT(*) FROM runs WHERE backtest_id = %s AND mode = 'BACKTEST'",
                    (backtest_id,),
                )
                run_count = (await cur.fetchone())[0]

                await cur.execute("SELECT COUNT(*) FROM outcomes")
                outcomes_after = (await cur.fetchone())[0]
                await cur.execute("SELECT COUNT(*) FROM price_snapshots")
                snapshots_after = (await cur.fetchone())[0]

        assert bt_row[0] == "SUCCESS"
        assert run_count == 3  # one run per trading day, Jan 10-12 2022 (Mon-Wed)

        # The whole point of this test: a backtest must be invisible to
        # the live paper-trading tables.
        assert outcomes_after == outcomes_before
        assert snapshots_after == snapshots_before

        metrics = await compute_backtest_metrics(pool, backtest_id)
        assert metrics["backtest_id"] == backtest_id
        # BUY-only fixture (portfolio_llm always proposes BUY, nothing
        # ever SELLs) -> every opened lot is still open at window end.
        assert metrics["closed_trades"] == 0
        assert metrics["open_trades_at_window_end"] >= 1
        assert metrics["realized_pnl"] == Decimal("0")
        assert metrics["win_rate"] is None  # no closed trades to rate

    finally:
        async with pool.connection() as conn:
            await conn.execute(
                "DELETE FROM historical_headlines WHERE symbol = %s", (symbol,)
            )
            # Cascades: backtests -> runs (via backtest_id, no FK cascade
            # defined, so runs are cleaned explicitly) -> decisions ->
            # agent_opinions/backtest_outcomes (ON DELETE CASCADE).
            await conn.execute(
                "DELETE FROM decisions WHERE run_id IN "
                "(SELECT id FROM runs WHERE backtest_id IN "
                "(SELECT id FROM backtests WHERE name = 'test backtest'))"
            )
            await conn.execute(
                "DELETE FROM runs WHERE backtest_id IN "
                "(SELECT id FROM backtests WHERE name = 'test backtest')"
            )
            await conn.execute(
                "DELETE FROM backtests WHERE name = 'test backtest'"
            )
            await conn.execute("DELETE FROM watchlist WHERE symbol = %s", (symbol,))


@pytest.mark.asyncio
async def test_position_cap_is_enforced_across_days(pool):
    """Regression test for a real bug found via a live pilot run, not by
    any test: risk_manager's MAX_POSITION_QTY cap (app/agent/risk_manager.py)
    never engaged during a backtest, because run_backtest()'s first write
    (the `backtests` row) executed directly on the connection instead of
    inside a `conn.transaction()` block. On a non-autocommit connection
    that implicitly opens a transaction that's never closed, so every
    later `async with conn.transaction():` in the day loop downgraded to
    a SAVEPOINT instead of a real commit — no day's writes became visible
    to BacktestRepository.list_positions()'s own connection until the
    whole function returned. Symptom in the wild: a real Q1 2022 AAPL
    pilot bought 25 times for 93 total shares against a 20-share cap,
    with risk_manager reporting current_qty=0 on every single day.

    This test proves the fix holds: a FakeLLM that always proposes BUY 10
    (regardless of what it's told) run across 4 trading days must be
    capped at exactly MAX_POSITION_QTY (20) shares open, with the 3rd and
    4th day's buys VETOed — not silently exceed it.
    """
    from app.agent.risk_manager import MAX_POSITION_QTY

    symbol = "CAPTEST"
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO watchlist (symbol, active) VALUES (%s, false) "
            "ON CONFLICT (symbol) DO NOTHING",
            (symbol,),
        )

    try:
        bars = [
            PriceBar(
                symbol=symbol,
                timestamp=datetime(2022, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
                open=Decimal("100") + i,
                high=Decimal("101") + i,
                low=Decimal("99") + i,
                close=Decimal("100") + i,
                volume=1_000_000,
            )
            for i in range(40)
        ]
        price_source = FakePriceSource(bars)
        headline_source = HistoricalHeadlineSource(pool)  # no headlines inserted -> HOLD short-circuit
        portfolio_llm = FakeLLM(
            TentativeDecision(
                action="BUY", quantity=10, confidence=0.9, reasoning="always buy 10"
            )
        )

        def make_graph(backtest_id: int):
            return build_decision_graph(
                price_source=price_source,
                headline_source=headline_source,
                repository=BacktestRepository(pool, backtest_id),
                portfolio_llm=portfolio_llm,
            )

        backtest_id = await run_backtest(
            pool,
            make_graph,
            name="test cap enforcement",
            symbols=[symbol],
            window_start=date(2022, 1, 3),
            window_end=date(2022, 1, 6),  # 4 trading days (Mon-Thu)
        )

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COALESCE(SUM(quantity), 0) FROM backtest_outcomes "
                    "WHERE backtest_id = %s AND status = 'OPEN'",
                    (backtest_id,),
                )
                total_open_qty = (await cur.fetchone())[0]

                await cur.execute(
                    "SELECT ao.opinion FROM agent_opinions ao "
                    "JOIN decisions d ON ao.decision_id = d.id "
                    "JOIN runs r ON d.run_id = r.id "
                    "WHERE r.backtest_id = %s AND ao.agent_name = 'risk_manager' "
                    "ORDER BY r.as_of",
                    (backtest_id,),
                )
                opinions = [row[0] for row in await cur.fetchall()]

        # The actual bug: without the fix, this would be 40 (4 days x 10
        # shares, cap never enforced) instead of capping at 20.
        assert total_open_qty == MAX_POSITION_QTY
        assert opinions == ["APPROVE", "APPROVE", "VETO", "VETO"]

    finally:
        async with pool.connection() as conn:
            await conn.execute(
                "DELETE FROM decisions WHERE run_id IN "
                "(SELECT id FROM runs WHERE backtest_id IN "
                "(SELECT id FROM backtests WHERE name = 'test cap enforcement'))"
            )
            await conn.execute(
                "DELETE FROM runs WHERE backtest_id IN "
                "(SELECT id FROM backtests WHERE name = 'test cap enforcement')"
            )
            await conn.execute(
                "DELETE FROM backtests WHERE name = 'test cap enforcement'"
            )
            await conn.execute("DELETE FROM watchlist WHERE symbol = %s", (symbol,))
