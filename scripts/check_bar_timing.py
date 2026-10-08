"""
Look-ahead check for backtest price data (V2, phase 07, step 1).

THE QUESTION
------------
`run_backtest` decides each simulated day at 12:00 UTC (08:00 New York,
before the open). `docs/backtesting-plan.md` requires that a decision only
sees the PRIOR day's close. `AlpacaPriceSource.get_recent_bars` asks Alpaca
for daily bars with `end=as_of` and trusts Alpaca to leave out the bar for
the decision day itself. Alpaca stamps daily bars at midnight New York
(04:00 or 05:00 UTC), which is BEFORE 12:00 UTC, so it is plausible that
the decision day's bar -- including its not-yet-happened close -- comes back.

OUTCOME (2026-09-28): it did. Both check days leaked. AlpacaPriceSource now
drops any bar whose 16:00 New York close is after as_of (bars_closed_before
in app/agent/data_sources.py); this script now reports OK for both days.

Nothing in the repo checks this. This script does, against the real API,
using the exact code path the backtest uses.

WHAT IT PRINTS
--------------
For a few past dates (one in US summer time, one in winter time), the last
bars returned for `as_of = <date> 12:00 UTC`. If the last bar's New York
date equals the decision date, the backtest was seeing that day's close.

Run (PowerShell, from the repo root, venv active):
    $env:PYTHONPATH="."; python scripts/check_bar_timing.py

Needs ALPACA_API_KEY / ALPACA_SECRET_KEY, same as a backtest. No database.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.agent.data_sources import AlpacaPriceSource

NY = ZoneInfo("America/New_York")

# Ordinary trading days (Thursdays), one in EDT and one in EST, inside the
# window the V1 backtests covered.
CHECK_DAYS = [date(2023, 6, 15), date(2023, 12, 14)]
SYMBOL = "AAPL"


def backtest_as_of(day: date) -> datetime:
    """Exactly what app/agent/backtest.py computes for a simulated day."""
    return datetime.combine(day, time.min, tzinfo=timezone.utc) + timedelta(hours=12)


async def main() -> int:
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        print("ALPACA_API_KEY / ALPACA_SECRET_KEY not set.")
        return 2

    source = AlpacaPriceSource(key, secret)
    leaked_any = False

    for day in CHECK_DAYS:
        as_of = backtest_as_of(day)
        bars = await source.get_recent_bars(SYMBOL, as_of, lookback_days=7)
        print(f"\nDecision day {day}  (as_of = {as_of.isoformat()})")
        for bar in bars[-3:]:
            ny_date = bar.timestamp.astimezone(NY).date()
            print(f"  bar {bar.timestamp.isoformat():<27} NY date {ny_date}  close {bar.close}")

        if not bars:
            print("  no bars returned -- cannot judge this day")
            continue

        last_ny_date = bars[-1].timestamp.astimezone(NY).date()
        if last_ny_date >= day:
            leaked_any = True
            print(f"  LEAK: last bar is the decision day itself ({last_ny_date}).")
        else:
            print(f"  OK: last bar is {last_ny_date}, before the decision day.")

    print()
    if leaked_any:
        print("RESULT: look-ahead present -- backtest decisions saw that day's close.")
        return 1
    print("RESULT: no look-ahead -- no bar from the decision day reached the backtest.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
