"""
Data source seam for the agent — the same Repository-protocol pattern
used for the database (app/repository/base.py), applied here to market
data instead.

Why this matters for backtesting (see docs/backtesting-plan.md): every
agent node depends on `PriceDataSource` / `HeadlineSource` as an
interface, never on Alpaca directly. Phase 03 wires in the `Alpaca*`
implementations below, which call live/recent data. A future
`HistoricalPriceSource` / `HistoricalHeadlineSource` (backed by Alpaca's
historical endpoints and/or the FNSPID dataset) can be swapped in for
backtesting without touching a single agent node.

NOTE: the Alpaca response shapes here (BarSet.data, NewsSet.data) were
confirmed against alpaca-py's model definitions but not yet exercised
against a live API call — that happens the first time real API keys are
wired in. If Alpaca's actual response nesting differs from what's coded
here, this file (not the agent nodes) is where the fix goes.
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
