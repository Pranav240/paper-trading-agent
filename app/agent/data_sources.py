"""
Data source seam for the agent — the same Repository-protocol pattern
used for the database (app/repository/base.py), applied here to market
data instead.

Why this matters for backtesting (see docs/backtesting-plan.md): every
agent node depends on `PriceDataSource` / `HeadlineSource` as an
interface, never on Alpaca directly. `AlpacaPriceSource` below doubles
as both the live AND historical price implementation — Alpaca's bars
endpoint takes an arbitrary `start`/`end`, so passing a years-old `as_of`
during a backtest already works with no separate class needed. Headlines
are different, not because Alpaca's News API lacks the history (Phase 03
already documented it going back to 2015) but for the reason the plan
gives: hundreds of simulated backtest days would mean hundreds of live
News API calls, coupling a backtest run to network reliability and rate
limits for no real benefit, when the answer for every one of those days
never changes once fetched. `HistoricalHeadlineSource` below reads from
a `historical_headlines` table instead (imported once, offline, from the
FNSPID dataset — see 004_historical_headlines.sql), so a backtest run is
fully reproducible and doesn't touch the network for headlines at all.
Agent nodes depend on the `HeadlineSource` protocol either way; which
implementation is wired in is a `build_*_data_sources()` decision, not
theirs.

Live integrations confirmed against real API calls (2026-08-31): Alpaca
price bars, Alpaca news, and the response shapes below (`BarSet.data`,
`NewsSet.data`) all matched what's coded here, with one fix needed — see
`AlpacaPriceSource.get_recent_bars`'s `feed=DataFeed.IEX` comment.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol

from alpaca.data.historical.news import NewsClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.enums import DataFeed
from alpaca.data.requests import NewsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel


class PriceBar(BaseModel):
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class Headline(BaseModel):
    symbol: str
    published_at: datetime
    headline: str
    source: str


class PriceDataSource(Protocol):
    async def get_recent_bars(
        self, symbol: str, as_of: datetime, lookback_days: int = 30
    ) -> list[PriceBar]: ...


class HeadlineSource(Protocol):
    async def get_recent_headlines(
        self, symbol: str, as_of: datetime, lookback_days: int = 3
    ) -> list[Headline]: ...


class AlpacaPriceSource:
    """Live implementation of PriceDataSource, backed by Alpaca's
    historical bars endpoint (which also serves "recent" data — there's
    no separate live-only endpoint needed for daily bars)."""

    def __init__(self, api_key: str, secret_key: str) -> None:
        self._client = StockHistoricalDataClient(api_key, secret_key)

    async def get_recent_bars(
        self, symbol: str, as_of: datetime, lookback_days: int = 30
    ) -> list[PriceBar]:
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=as_of - timedelta(days=lookback_days),
            end=as_of,
            # alpaca-py defaults to the SIP (consolidated) feed, which the
            # free "Basic" market data plan this project uses cannot query
            # for recent data — confirmed via a live 403 during Phase 03
            # testing ("subscription does not permit querying recent SIP
            # data"). IEX is the single-exchange feed Basic accounts DO get
            # for free; daily-bar technical analysis doesn't need
            # consolidated-tape precision, so this isn't a real accuracy
            # tradeoff for what this node uses it for.
            feed=DataFeed.IEX,
        )
        # alpaca-py's client methods are synchronous; run_in_executor
        # would be the "proper" async wrapper, but for a once-a-day
        # agent cycle a blocking call here is a fine tradeoff — it's
        # not worth the complexity until Phase 07's continuous loop
        # actually needs many concurrent requests.
        bar_set = self._client.get_stock_bars(request)
        bars = bar_set.data.get(symbol, [])
        return [
            PriceBar(
                symbol=symbol,
                timestamp=bar.timestamp,
                open=Decimal(str(bar.open)),
                high=Decimal(str(bar.high)),
                low=Decimal(str(bar.low)),
                close=Decimal(str(bar.close)),
                volume=int(bar.volume),
            )
            for bar in bars
        ]


class AlpacaHeadlineSource:
    """Live implementation of HeadlineSource, backed by Alpaca's News API
    (free tier, same account as price data, history back to 2015)."""

    def __init__(self, api_key: str, secret_key: str) -> None:
        self._client = NewsClient(api_key, secret_key)

    async def get_recent_headlines(
        self, symbol: str, as_of: datetime, lookback_days: int = 3
    ) -> list[Headline]:
        request = NewsRequest(
            symbols=symbol,
            start=as_of - timedelta(days=lookback_days),
            end=as_of,
            limit=20,
        )
        news_set = self._client.get_news(request)
        articles = news_set.data.get("news", [])
        return [
            Headline(
                symbol=symbol,
                published_at=article.created_at,
                headline=article.headline,
                source=article.source,
            )
            for article in articles
        ]


class HistoricalHeadlineSource:
    """Backtest implementation of HeadlineSource, backed by the imported
    FNSPID data in `historical_headlines` (see
    db/migrations/004_historical_headlines.sql) instead of a live API
    call — see this module's docstring for why.

    Mirrors AlpacaHeadlineSource.get_recent_headlines's exact signature
    and return shape on purpose: agent nodes call whichever HeadlineSource
    they were given and can't tell which one is live vs. historical.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def get_recent_headlines(
        self, symbol: str, as_of: datetime, lookback_days: int = 3
    ) -> list[Headline]:
        # `published_at < as_of` (strictly less than, not <=) is the
        # look-ahead-bias guard from docs/backtesting-plan.md's checklist:
        # a headline published exactly at as_of wasn't necessarily readable
        # yet at decision time, so treat as_of as the earliest excluded
        # moment rather than the latest included one.
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT symbol, published_at, headline, source
                    FROM historical_headlines
                    WHERE symbol = %s
                      AND published_at < %s
                      AND published_at >= %s
                    ORDER BY published_at DESC
                    """,
                    (symbol, as_of, as_of - timedelta(days=lookback_days)),
                )
                rows = await cur.fetchall()
        return [Headline(**row) for row in rows]


def build_live_data_sources() -> tuple[AlpacaPriceSource, AlpacaHeadlineSource]:
    """Reads ALPACA_API_KEY / ALPACA_SECRET_KEY from the environment and
    constructs both live data sources. Raises a clear error rather than
    a confusing downstream failure if the keys aren't set."""
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError(
            "ALPACA_API_KEY / ALPACA_SECRET_KEY not set. Copy .env.example "
            "to .env and fill in your Alpaca paper trading API keys."
        )
    return (
        AlpacaPriceSource(api_key, secret_key),
        AlpacaHeadlineSource(api_key, secret_key),
    )


def build_historical_data_sources(
    pool: AsyncConnectionPool,
) -> tuple[AlpacaPriceSource, HistoricalHeadlineSource]:
    """Backtest counterpart to build_live_data_sources().

    Prices: still AlpacaPriceSource, still hitting the real Alpaca API —
    per this module's docstring, Alpaca's bars endpoint already serves
    arbitrary historical `start`/`end` ranges, so there's no separate
    historical price class to build. This does mean a backtest run still
    needs ALPACA_API_KEY / ALPACA_SECRET_KEY set and still makes network
    calls for price data (just not for headlines).

    Headlines: HistoricalHeadlineSource, reading the imported FNSPID rows
    from `historical_headlines` via the given connection pool — no
    network call, no rate limit, fully reproducible.
    """
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError(
            "ALPACA_API_KEY / ALPACA_SECRET_KEY not set. A backtest still "
            "needs these for historical price data, even though headlines "
            "come from the local database instead of a live API call."
        )
    return (
        AlpacaPriceSource(api_key, secret_key),
        HistoricalHeadlineSource(pool),
    )
