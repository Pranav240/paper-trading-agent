"""
Kupiec backtest of the risk manager's VaR against a naive baseline
(V2, phase 07, step 6a). No LLM calls, no database writes.

WHAT IS COMPARED
----------------
For every trading day D in a window, three 95% 1-day VaR forecasts made
with closes through D-1 only (the same filter the backtest uses):

- historical: risk_math.historical_var over the last 250 returns -- the
  number the risk manager actually budgets with.
- parametric: normal VaR, same 250 returns -- for comparison.
- naive: a flat 2% every day. The trivial baseline. (It is also exactly
  what the V1 node implies: a 2% VaR makes the budget allow all 20
  shares, i.e. no VaR rule at all.)

Each is scored against the realized return close(D) / close(D-1) - 1.
A breach is a loss bigger than the forecast. Kupiec POF tests whether
the breach rate is 5%.

PRE-REGISTERED (written and committed before this script was first run)
------------------------------------------------------------------------
- Primary window: run #4's, 2022-06-01 .. 2023-06-30, AAPL.
- "Historical VaR beats naive 2%" on a window means: Kupiec does not
  reject historical (p >= 0.05) AND does reject naive (p < 0.05).
- Both not rejected: "no difference detectable", reported with the
  test's power (docs/engineering-log.md, phase 07 step 2) -- NOT a pass.
- Historical rejected: it fails, whatever naive does.
- Secondary windows are reported, but do not change the primary verdict:
  out-of-sample 2023-07-01 .. 2023-12-31, and a long 2017-2023 window
  where the test has real power.

Bars are fetched once per window and sliced per day, rather than one API
call per day as the risk node does; the session-close filter is
date-based, so the result is identical. `--check-backtest ID` verifies
that against a backtest's stored var_forecasts rows.

Run (PowerShell, repo root):
    $env:PYTHONPATH="."; .\\papertrading\\Scripts\\python.exe scripts/evaluate_var.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, datetime, time, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.agent.data_sources import NEW_YORK, AlpacaPriceSource
from app.agent.risk_math import (
    KUPIEC_ALPHA,
    VAR_WINDOW,
    historical_var,
    kupiec_acceptance_region,
    kupiec_power,
    kupiec_pvalue,
    parametric_var,
    simple_returns,
)

NAIVE_VAR = 0.02
WINDOWS = [
    ("PRIMARY run #4 window", date(2022, 6, 1), date(2023, 6, 30)),
    ("out-of-sample window", date(2023, 7, 1), date(2023, 12, 31)),
    ("long window", date(2017, 1, 1), date(2023, 12, 31)),
]


async def forecasts(source, symbol: str, start: date, end: date) -> list[dict]:
    fetch_end = datetime.combine(end + timedelta(days=1), time(12), tzinfo=timezone.utc)
    fetch_days = (end - start).days + 450
    bars = await source.get_recent_bars(symbol, fetch_end, lookback_days=fetch_days)
    days = [b.timestamp.astimezone(NEW_YORK).date() for b in bars]
    closes = [float(b.close) for b in bars]

    out = []
    for i, day in enumerate(days):
        if not start <= day <= end or i == 0:
            continue
        # Closes strictly before D: exactly what bars_closed_before leaves
        # for as_of = D 12:00 UTC.
        returns = simple_returns(closes[:i])
        hist = historical_var(returns)
        if hist is None:
            continue
        out.append(
            {
                "day": day,
                "historical": hist,
                "parametric": parametric_var(returns),
                "naive": NAIVE_VAR,
                "realized": closes[i] / closes[i - 1] - 1,
            }
        )
    return out


def score(rows: list[dict], model: str) -> tuple[int, float]:
    breaches = sum(1 for r in rows if r["realized"] < -r[model])
    return breaches, kupiec_pvalue(breaches, len(rows))


def verdict(p_hist: float, p_naive: float) -> str:
    if p_hist < KUPIEC_ALPHA:
        return "FAIL: historical VaR rejected by Kupiec"
    if p_naive < KUPIEC_ALPHA:
        return "BEATS naive: historical not rejected, naive rejected"
    return "NO DIFFERENCE DETECTABLE: neither rejected"


async def check_backtest(source, symbol: str, backtest_id: int) -> None:
    import psycopg

    url = os.environ.get(
        "DATABASE_URL",
        "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
    )
    async with await psycopg.AsyncConnection.connect(url) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT as_of, var FROM var_forecasts "
                "WHERE backtest_id = %s AND symbol = %s ORDER BY as_of",
                (backtest_id, symbol),
            )
            stored = await cur.fetchall()
    if not stored:
        print(f"backtest {backtest_id}: no var_forecasts rows")
        return
    start, end = stored[0][0].date(), stored[-1][0].date()
    mine = {r["day"]: r["historical"] for r in await forecasts(source, symbol, start, end)}
    worst, missing = 0.0, 0
    for as_of, var in stored:
        day = as_of.astimezone(NEW_YORK).date()
        if day not in mine:
            missing += 1  # market holiday: the backtest still ran that weekday
            continue
        worst = max(worst, abs(float(var) - mine[day]))
    print(
        f"backtest {backtest_id}: {len(stored)} stored forecasts, "
        f"{missing} on non-trading days, max |stored - recomputed| = {worst:.2e}"
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--check-backtest", type=int)
    args = parser.parse_args()

    source = AlpacaPriceSource(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])

    if args.check_backtest:
        await check_backtest(source, args.symbol, args.check_backtest)
        return

    print(f"{args.symbol}  95% 1-day VaR, {VAR_WINDOW}-return window, naive = {NAIVE_VAR:.0%}\n")
    for label, start, end in WINDOWS:
        rows = await forecasts(source, args.symbol, start, end)
        n = len(rows)
        if not n:
            print(f"{label}: no data\n")
            continue
        low, high = kupiec_acceptance_region(n)
        print(f"{label}: {rows[0]['day']} .. {rows[-1]['day']}, {n} days")
        print(f"  Kupiec accepts {low}-{high} breaches (expected {0.05 * n:.1f}); "
              f"power vs 7.5% / 10% true rate: {kupiec_power(n, 0.075):.2f} / {kupiec_power(n, 0.10):.2f}")
        results = {}
        for model in ("historical", "parametric", "naive"):
            breaches, p = score(rows, model)
            results[model] = p
            mean_var = sum(r[model] for r in rows) / n
            print(f"  {model:<11} mean VaR {mean_var:6.2%}  breaches {breaches:>3} "
                  f"({breaches / n:5.1%})  Kupiec p = {p:.3f}")
        print(f"  -> {verdict(results['historical'], results['naive'])}\n")


if __name__ == "__main__":
    asyncio.run(main())
