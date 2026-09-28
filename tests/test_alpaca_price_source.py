"""
Look-ahead filter in AlpacaPriceSource (app/agent/data_sources.py).

Alpaca stamps daily bars at midnight New York, so `end=as_of` alone
returned the decision day's own bar in backtests (scripts/check_bar_timing.py
confirmed this live). These tests stub the Alpaca client with the bar
timestamps it really returned and check that only bars whose 16:00 New
York session close is at or before `as_of` come back. No network.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.agent.data_sources import AlpacaPriceSource


def raw_bar(ts: datetime, close: float) -> SimpleNamespace:
    return SimpleNamespace(timestamp=ts, open=close, high=close, low=close, close=close, volume=1)


def source_returning(raw_bars: list[SimpleNamespace]) -> AlpacaPriceSource:
    source = AlpacaPriceSource.__new__(AlpacaPriceSource)
    source._client = SimpleNamespace(
        get_stock_bars=lambda request: SimpleNamespace(data={"AAPL": raw_bars})
    )
    return source


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# Real AAPL bars from the live check (EDT: stamped 04:00 UTC).
SUMMER_BARS = [
    raw_bar(utc(2023, 6, 13, 4), 183.31),
    raw_bar(utc(2023, 6, 14, 4), 183.95),
    raw_bar(utc(2023, 6, 15, 4), 185.99),
]
# EST: stamped 05:00 UTC.
WINTER_BARS = [
    raw_bar(utc(2023, 12, 12, 5), 194.70),
    raw_bar(utc(2023, 12, 13, 5), 197.86),
    raw_bar(utc(2023, 12, 14, 5), 198.16),
]


async def test_backtest_decision_time_drops_same_day_bar_summer():
    bars = await source_returning(SUMMER_BARS).get_recent_bars("AAPL", utc(2023, 6, 15, 12))
    assert [str(b.close) for b in bars] == ["183.31", "183.95"]


async def test_backtest_decision_time_drops_same_day_bar_winter():
    bars = await source_returning(WINTER_BARS).get_recent_bars("AAPL", utc(2023, 12, 14, 12))
    assert [str(b.close) for b in bars] == ["194.7", "197.86"]


async def test_bar_kept_exactly_at_session_close():
    # 16:00 EDT on 2023-06-15 is 20:00 UTC.
    bars = await source_returning(SUMMER_BARS).get_recent_bars("AAPL", utc(2023, 6, 15, 20))
    assert len(bars) == 3


async def test_bar_dropped_one_minute_before_session_close():
    bars = await source_returning(SUMMER_BARS).get_recent_bars("AAPL", utc(2023, 6, 15, 19, 59))
    assert len(bars) == 2


async def test_winter_close_uses_est_offset():
    # 16:00 EST on 2023-12-14 is 21:00 UTC; 20:30 UTC is still in session.
    source = source_returning(WINTER_BARS)
    assert len(await source.get_recent_bars("AAPL", utc(2023, 12, 14, 20, 30))) == 2
    assert len(await source.get_recent_bars("AAPL", utc(2023, 12, 14, 21))) == 3
