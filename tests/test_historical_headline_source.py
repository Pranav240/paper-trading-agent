"""
Integration test for HistoricalHeadlineSource (app/agent/data_sources.py).

Every other test in this suite runs against fakes with zero Postgres
involvement (see tests/conftest.py's docstring). This one is deliberately
different: HistoricalHeadlineSource's entire job is a SQL WHERE clause
(the look-ahead-bias filter), and a fake pool/cursor can't catch a bug in
that SQL — only a real query against a real table can. So this test talks
to a real Postgres database.

To keep "pytest with zero services running" true for everyone else, this
module skips itself (rather than failing) if it can't connect — see the
`pool` fixture below. Run `db/migrate.py` against a real DATABASE_URL
first for this test to actually execute.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest
import psycopg
from psycopg_pool import AsyncConnectionPool

from app.agent.data_sources import HistoricalHeadlineSource

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

    # Isolate this test from any other data in the table: wipe rows for
    # the one symbol this test uses, before and after.
    async with test_pool.connection() as conn:
        await conn.execute(
            "DELETE FROM historical_headlines WHERE symbol = 'TEST'"
        )

    yield test_pool

    async with test_pool.connection() as conn:
        await conn.execute(
            "DELETE FROM historical_headlines WHERE symbol = 'TEST'"
        )
    await test_pool.close()


async def _insert_headline(
    pool: AsyncConnectionPool, *, published_at: datetime, headline: str
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO historical_headlines (symbol, published_at, headline, source)
            VALUES ('TEST', %s, %s, 'fnspid')
            """,
            (published_at, headline),
        )


@pytest.mark.asyncio
async def test_excludes_headlines_at_or_after_as_of(pool):
    """The look-ahead-bias guard: published_at must be strictly < as_of.
    A headline published exactly at as_of, or after it, must not appear —
    it wasn't necessarily readable yet at decision time."""
    as_of = datetime(2022, 6, 15, tzinfo=timezone.utc)
    await _insert_headline(
        pool, published_at=as_of, headline="published exactly at as_of"
    )
    await _insert_headline(
        pool, published_at=as_of + timedelta(hours=1), headline="published after as_of"
    )
    await _insert_headline(
        pool,
        published_at=as_of - timedelta(hours=1),
        headline="published before as_of",
    )

    source = HistoricalHeadlineSource(pool)
    results = await source.get_recent_headlines("TEST", as_of, lookback_days=3)

    headlines = {h.headline for h in results}
    assert headlines == {"published before as_of"}


@pytest.mark.asyncio
async def test_excludes_headlines_older_than_lookback_window(pool):
    as_of = datetime(2022, 6, 15, tzinfo=timezone.utc)
    await _insert_headline(
        pool, published_at=as_of - timedelta(days=1), headline="within window"
    )
    await _insert_headline(
        pool, published_at=as_of - timedelta(days=10), headline="too old"
    )

    source = HistoricalHeadlineSource(pool)
    results = await source.get_recent_headlines("TEST", as_of, lookback_days=3)

    headlines = {h.headline for h in results}
    assert headlines == {"within window"}


@pytest.mark.asyncio
async def test_orders_most_recent_first(pool):
    as_of = datetime(2022, 6, 15, tzinfo=timezone.utc)
    await _insert_headline(
        pool, published_at=as_of - timedelta(hours=2), headline="older"
    )
    await _insert_headline(
        pool, published_at=as_of - timedelta(hours=1), headline="newer"
    )

    source = HistoricalHeadlineSource(pool)
    results = await source.get_recent_headlines("TEST", as_of, lookback_days=3)

    assert [h.headline for h in results] == ["newer", "older"]


@pytest.mark.asyncio
async def test_only_returns_headlines_for_requested_symbol(pool):
    as_of = datetime(2022, 6, 15, tzinfo=timezone.utc)
    await _insert_headline(
        pool, published_at=as_of - timedelta(hours=1), headline="TEST symbol"
    )
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO historical_headlines (symbol, published_at, headline, source)
            VALUES ('OTHER', %s, 'other symbol', 'fnspid')
            """,
            (as_of - timedelta(hours=1),),
        )

    try:
        source = HistoricalHeadlineSource(pool)
        results = await source.get_recent_headlines("TEST", as_of, lookback_days=3)
        assert [h.headline for h in results] == ["TEST symbol"]
    finally:
        async with pool.connection() as conn:
            await conn.execute(
                "DELETE FROM historical_headlines WHERE symbol = 'OTHER'"
            )
