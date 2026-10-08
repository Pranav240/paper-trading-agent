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
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

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


NEW_YORK = ZoneInfo("America/New_York")
# Regular-session close. Early-close days (13:00) are treated as 16:00,
# which can only drop a bar that was already final, never leak one.
SESSION_CLOSE = time(16, 0)


def bars_closed_before(bars: list[PriceBar], as_of: datetime) -> list[PriceBar]:
    """Keep only daily bars whose session had closed by `as_of`.

    Alpaca stamps a daily bar at midnight New York (04:00/05:00 UTC), so
    `end=as_of` alone returns the decision day's own bar -- close included --
    for any as_of after that midnight. Confirmed live by
    scripts/check_bar_timing.py (2026-09-28): as_of 12:00 UTC got that
    day's bar. docs/backtesting-plan.md requires prior-day close only.
    """
    kept = []
    for bar in bars:
        session_day = bar.timestamp.astimezone(NEW_YORK).date()
        closed_at = datetime.combine(session_day, SESSION_CLOSE, tzinfo=NEW_YORK)
        if closed_at <= as_of:
            kept.append(bar)
    return kept


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
        return bars_closed_before([
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
        ], as_of)


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


# FNSPID timestamps are dates, not times: 99.7% of `historical_headlines`
# rows are stamped exactly 00:00 UTC (found 2026-10-06; see the engineering
# log). A headline stamped D 00:00 UTC was published at some unknown time
# ON date D -- possibly after the close -- so treating the stamp as the
# publication time let a decision at D 08:00 New York read that evening's
# "Apple was the worst stock in the Dow today". These two SQL expressions
# are the fix, shared by every query that reads headlines:
#
# HEADLINE_AVAILABLE_AT: when a headline could first have been read. A
# date-only stamp becomes available only once its whole date has ended in
# New York, the latest time zone the date could be in; a stamp with a real
# time is available from that time. (A genuine 00:00:00 UTC publication is
# delayed a day: conservative, never a leak.)
HEADLINE_AVAILABLE_AT = (
    "(CASE WHEN (published_at AT TIME ZONE 'UTC')::time = time '00:00' "
    "THEN (((published_at AT TIME ZONE 'UTC')::date + 1)::timestamp "
    "AT TIME ZONE 'America/New_York') ELSE published_at END)"
)
# HEADLINE_DATE: the calendar date a headline belongs to -- its stamped
# date if date-only, its New York date otherwise.
HEADLINE_DATE = (
    "(CASE WHEN (published_at AT TIME ZONE 'UTC')::time = time '00:00' "
    "THEN (published_at AT TIME ZONE 'UTC')::date "
    "ELSE (published_at AT TIME ZONE 'America/New_York')::date END)"
)


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
        # Look-ahead guard: a headline is visible only if it was AVAILABLE
        # strictly before as_of (HEADLINE_AVAILABLE_AT above), not merely
        # stamped before it. For a backtest decision at D 12:00 UTC this
        # means headlines dated D-1 or earlier; the lookback window is
        # measured on availability too. The plain `published_at` bounds
        # only narrow the scan for the index.
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    f"""
                    SELECT symbol, published_at, headline, source
                    FROM historical_headlines
                    WHERE symbol = %(symbol)s
                      AND published_at < %(as_of)s
                      AND published_at >= %(scan_from)s
                      AND {HEADLINE_AVAILABLE_AT} < %(as_of)s
                      AND {HEADLINE_AVAILABLE_AT} >= %(from)s
                    ORDER BY published_at DESC
                    """,
                    {
                        "symbol": symbol,
                        "as_of": as_of,
                        "from": as_of - timedelta(days=lookback_days),
                        "scan_from": as_of - timedelta(days=lookback_days + 2),
                    },
                )
                rows = await cur.fetchall()
        from app.agent import trace  # local: trace imports nothing from here

        trace.record(
            node="sentiment_analyst", kind="retrieval",
            input={"symbol": symbol, "as_of": as_of.isoformat(), "lookback_days": lookback_days,
                   "rule": "available (HEADLINE_AVAILABLE_AT) strictly before as_of"},
            output={"results": [
                {"published_at": r["published_at"].isoformat(), "headline": r["headline"]}
                for r in rows
            ]},
        )
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


def alpaca_trading_days(window_start: date, window_end: date) -> list[date]:
    """NYSE trading days in [window_start, window_end], from Alpaca's market
    calendar. Replaces "every weekday", which put V1 backtests through
    market holidays (11 in run #4's window: 283 weekdays, 272 sessions)."""
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetCalendarRequest

    client = TradingClient(
        os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], paper=True
    )
    calendar = client.get_calendar(GetCalendarRequest(start=window_start, end=window_end))
    return [session.date for session in calendar]


class OpenPriceBook:
    """Day D's opening price per symbol: the price a backtest trade decided
    before the open on D fills at (docs/backtesting-plan.md, "fill at the
    next bar's open"). Used for execution only, never shown to an agent.

    Bars for the whole window are fetched once per symbol and cached.
    """

    def __init__(self, price_source: PriceDataSource, window_start: date, window_end: date) -> None:
        self._source = price_source
        self._start = window_start
        self._end = window_end
        self._opens: dict[str, dict[date, Decimal]] = {}

    async def open_on(self, symbol: str, day: date) -> Decimal | None:
        if symbol not in self._opens:
            # Noon UTC the day after window_end: window_end's session has
            # closed, so bars_closed_before keeps its bar.
            as_of = datetime.combine(
                self._end + timedelta(days=1), time(12), tzinfo=timezone.utc
            )
            bars = await self._source.get_recent_bars(
                symbol, as_of, lookback_days=(as_of.date() - self._start).days + 7
            )
            self._opens[symbol] = {
                bar.timestamp.astimezone(NEW_YORK).date(): bar.open for bar in bars
            }
        return self._opens[symbol].get(day)
