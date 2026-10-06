"""
Build phase 09's test set (docs/phase09-plan.md, section 1) by its fixed
rule, plus the headline fixture CI needs to re-run retrieval offline.

Writes, under eval/:
- golden_days.json: the 40 news days, each with its candidate headlines
  (union of fts and recent top 8) in a fixed shuffled order, and which
  method ranked each where -- the page that labels them never shows that;
  plus the no-news driver days.
- headline_fixture.json: every headline in every test day's retrieval
  window, so retrieval can be re-ranked against a fresh database.

No LLM calls. Run:
    $env:PYTHONPATH="."; .\\papertrading\\Scripts\\python.exe scripts/build_golden_days.py
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.agent.data_sources import HEADLINE_DATE, NEW_YORK, AlpacaPriceSource
from app.agent.explainer import DRIVER_DAYS, TRIGGER_FLAGS, HEADLINES_PER_DAY, HeadlineRetriever
from app.agent.risk_math import simple_returns, tail_losses

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)
SYMBOL = "AAPL"
START, END = date(2021, 7, 1), date(2023, 12, 31)
N_DAYS = 40
DRIVER_BACKTESTS = (45, 71)
OUT = Path("eval")


async def main() -> None:
    pool = AsyncConnectionPool(DATABASE_URL, open=False)
    await pool.open(wait=True, timeout=10)
    source = AlpacaPriceSource(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])

    as_of_end = datetime.combine(END + timedelta(days=2), time(12), tzinfo=timezone.utc)
    bars = await source.get_recent_bars(SYMBOL, as_of_end, lookback_days=(END - START).days + 450)
    days = [b.timestamp.astimezone(NEW_YORK).date() for b in bars]
    rets = simple_returns([b.close for b in bars])
    daily = [(d, r) for d, r in zip(days[1:], rets) if START <= d <= END]

    async def headlines_in(day: date) -> list[dict]:
        # Headlines DATED day-1 or day: the explainer's retrieval window
        # (HEADLINE_DATE; FNSPID stamps are dates, not times).
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    f"SELECT id, symbol, published_at, headline FROM historical_headlines "
                    f"WHERE symbol = %s AND {HEADLINE_DATE} BETWEEN %s AND %s ORDER BY id",
                    (SYMBOL, day - timedelta(days=1), day),
                )
                return await cur.fetchall()

    # The explainer's own driver days, to mark overlap and find no-news ones.
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT as_of FROM var_forecasts WHERE backtest_id = ANY(%s) AND risk_flags && %s",
            (list(DRIVER_BACKTESTS), list(TRIGGER_FLAGS)),
        )
        decision_times = [row[0] for row in await cur.fetchall()]
    closes = [b.close for b in bars]
    driver_days: set[date] = set()
    for as_of in decision_times:
        n = sum(1 for d in days if d < as_of.astimezone(NEW_YORK).date())
        driver_days |= {d for d, _ in (tail_losses(simple_returns(closes[:n]), days[1:n]) or [])[:DRIVER_DAYS]}

    fts, recent = HeadlineRetriever(pool, "fts"), HeadlineRetriever(pool, "recent")
    golden, fixture = [], {}
    for day, ret in sorted(daily, key=lambda p: p[1]):
        if len(golden) == N_DAYS:
            break
        in_window = await headlines_in(day)
        if not in_window:
            continue
        # Two days after D: every headline dated D has become available.
        as_of = datetime.combine(day + timedelta(days=2), time(12), tzinfo=timezone.utc)
        a = await fts.around(SYMBOL, day, as_of, HEADLINES_PER_DAY)
        b = await recent.around(SYMBOL, day, as_of, HEADLINES_PER_DAY)
        ranks: dict[int, dict] = {}
        for method, picks in (("fts", a), ("recent", b)):
            for i, h in enumerate(picks, start=1):
                entry = ranks.setdefault(h.id, {"id": h.id, "published_at": h.published_at.isoformat(),
                                                "headline": h.headline, "fts_rank": None, "recent_rank": None})
                entry[f"{method}_rank"] = i
        candidates = sorted(ranks.values(), key=lambda c: c["id"])
        random.Random(f"phase09-{day}").shuffle(candidates)  # fixed, method-blind order
        golden.append({
            "date": str(day),
            "return": round(ret, 6),
            "is_driver_day": day in driver_days,
            "candidates": candidates,
        })
        for h in in_window:
            fixture[h["id"]] = {**h, "published_at": h["published_at"].isoformat()}

    no_news = []
    for d in sorted(driver_days):
        if not await headlines_in(d):
            no_news.append(str(d))
    await pool.close()

    OUT.mkdir(exist_ok=True)
    meta = {
        "rule": f"{SYMBOL}'s {N_DAYS} largest daily losses {START}..{END} with >=1 headline "
                "dated D-1 or D; candidates = union of fts and recent top "
                f"{HEADLINES_PER_DAY}; see docs/phase09-plan.md",
        "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (OUT / "golden_days.json").write_text(
        json.dumps({**meta, "days": golden, "no_news_driver_days": no_news}, indent=1), encoding="utf-8"
    )
    (OUT / "headline_fixture.json").write_text(
        json.dumps(sorted(fixture.values(), key=lambda h: h["id"]), indent=0), encoding="utf-8"
    )
    n_cand = sum(len(d["candidates"]) for d in golden)
    print(f"{len(golden)} news days ({sum(d['is_driver_day'] for d in golden)} are explainer driver days), "
          f"returns {golden[0]['return']:+.2%} .. {golden[-1]['return']:+.2%}")
    print(f"{n_cand} candidate headlines to label (avg {n_cand / len(golden):.1f}/day); "
          f"{len(fixture)} fixture headlines; no-news driver days: {no_news}")


if __name__ == "__main__":
    asyncio.run(main())
