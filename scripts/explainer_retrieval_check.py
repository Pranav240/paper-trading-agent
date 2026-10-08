"""
Retrieval-only dry run of the phase 08 explainer over a finished backtest.
No LLM calls, no writes: what the explainer WOULD have been shown.

For every decision the VaR budget changed (var_budget_scale / _veto), it
recomputes the VaR's worst-loss days from prices (the same tail_losses the
risk node now records), retrieves headlines for the DRIVER_DAYS worst with
both methods, and reports:

- coverage: how many driver days have any headline at all;
- whether the top-ranked headline names the company (fts vs recent);
- how much the two methods' picks overlap;
- the prompt size, so a paid run's cost is estimated from real prompts.

Run:
    $env:PYTHONPATH="."; .\\papertrading\\Scripts\\python.exe scripts/explainer_retrieval_check.py 45
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, time, timedelta, timezone
from statistics import mean

from dotenv import load_dotenv

load_dotenv()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import psycopg
from psycopg_pool import AsyncConnectionPool

from app.agent.data_sources import NEW_YORK, AlpacaPriceSource
from app.agent.explainer import (
    COMPANY_TERMS,
    DRIVER_DAYS,
    SYSTEM_PROMPT,
    TRIGGER_FLAGS,
    HeadlineRetriever,
)
from app.agent.risk_math import simple_returns, tail_losses

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)
CHARS_PER_TOKEN = 3.5  # rough, conservative; the real run's llm_usage replaces it


async def main() -> None:
    backtest_id = int(sys.argv[1])
    pool = AsyncConnectionPool(DATABASE_URL, open=False)
    await pool.open(wait=True, timeout=10)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT symbol, as_of FROM var_forecasts WHERE backtest_id = %s "
            "AND risk_flags && %s ORDER BY as_of",
            (backtest_id, list(TRIGGER_FLAGS)),
        )
        decisions = await cur.fetchall()
    if not decisions:
        raise SystemExit(f"No VaR-changed decisions in backtest {backtest_id}.")

    symbol = decisions[0][0]
    first, last = decisions[0][1], decisions[-1][1]
    source = AlpacaPriceSource(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
    bars = await source.get_recent_bars(
        symbol, last + timedelta(days=1), lookback_days=(last - first).days + 420
    )
    days = [b.timestamp.astimezone(NEW_YORK).date() for b in bars]
    closes = [b.close for b in bars]
    names = COMPANY_TERMS.get(symbol, [symbol.lower()])

    fts, recent = HeadlineRetriever(pool, "fts"), HeadlineRetriever(pool, "recent")
    driver_days = covered = fts_names = recent_names = 0
    overlaps, prompt_chars = [], []
    for _, as_of in decisions:
        # Bars strictly before the decision day, as the risk node sees them.
        n = sum(1 for d in days if d < as_of.astimezone(NEW_YORK).date())
        tail = tail_losses(simple_returns(closes[:n]), days[1:n]) or []
        chars = len(SYSTEM_PROMPT) + 400
        for day, ret in tail[:DRIVER_DAYS]:
            a = await fts.around(symbol, day, as_of)
            b = await recent.around(symbol, day, as_of)
            driver_days += 1
            chars += 40 + sum(len(h.headline) + 10 for h in a)
            if a:
                covered += 1
                fts_names += any(t in a[0].headline.lower() for t in names)
                recent_names += any(t in b[0].headline.lower() for t in names)
                overlaps.append(len({h.id for h in a} & {h.id for h in b}) / len(a))
        prompt_chars.append(chars)
    await pool.close()

    tokens_in = mean(prompt_chars) / CHARS_PER_TOKEN
    tokens_out = 350  # summary + up to 5 driver entries; assumed, not measured
    per_call = tokens_in / 1e6 * 1.00 + tokens_out / 1e6 * 5.00  # Haiku 4.5 list prices
    print(f"backtest {backtest_id}: {len(decisions)} VaR-changed decisions, {driver_days} driver days")
    print(f"  driver days with any headline: {covered}/{driver_days} ({covered / driver_days:.0%})")
    print(f"  top headline names {'/'.join(names)}: fts {fts_names}/{covered}, recent {recent_names}/{covered}")
    print(f"  fts/recent overlap in picks: mean {mean(overlaps):.0%}")
    print(f"  prompt ~{tokens_in:.0f} tokens in; est. ${per_call:.4f}/call on Haiku 4.5, "
          f"${per_call * len(decisions):.2f} for all {len(decisions)}, ${per_call * 20:.3f} for 20")


if __name__ == "__main__":
    asyncio.run(main())
