"""
Phase 09 regression check (docs/phase09-plan.md, section 6): retrieval
must reproduce, exactly, the rankings the labelled test set was built and
scored on (eval/golden_days.json). Any change to retrieval -- query terms,
window, ranking -- fails here until the test set is rebuilt and the
retrieval test (scripts/eval_retrieval.py) re-run on purpose.

Real Postgres (skips if none is reachable; CI provides one). The fixture's
1,415 AAPL headlines are loaded under a separate symbol so this never
touches the real AAPL rows, and AAPL's company terms are passed in.
"""

import json
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from app.agent.explainer import COMPANY_TERMS, HEADLINES_PER_DAY, HeadlineRetriever

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)
SYMBOL = "GOLDFIX"
GOLDEN = json.loads(Path("eval/golden_days.json").read_text(encoding="utf-8"))
FIXTURE = json.loads(Path("eval/headline_fixture.json").read_text(encoding="utf-8"))


@pytest.fixture
async def loaded():
    try:
        pool = AsyncConnectionPool(DATABASE_URL, open=False)
        await pool.open(wait=True, timeout=3)
    except psycopg.OperationalError:
        pytest.skip("no reachable Postgres database — skipping DB integration test")
        return
    new_to_old: dict[int, int] = {}
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM historical_headlines WHERE symbol = %s", (SYMBOL,))
        async with conn.cursor() as cur:
            for h in FIXTURE:
                await cur.execute(
                    "INSERT INTO historical_headlines (symbol, published_at, headline) "
                    "VALUES (%s, %s, %s) RETURNING id",
                    (SYMBOL, datetime.fromisoformat(h["published_at"]), h["headline"]),
                )
                new_to_old[(await cur.fetchone())[0]] = h["id"]
    yield pool, new_to_old
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM historical_headlines WHERE symbol = %s", (SYMBOL,))
    await pool.close()


async def test_retrieval_reproduces_the_labelled_rankings(loaded):
    pool, new_to_old = loaded
    terms = COMPANY_TERMS["AAPL"]
    mismatches = []
    for day in GOLDEN["days"]:
        d = date.fromisoformat(day["date"])
        as_of = datetime.combine(d + timedelta(days=2), time(12), tzinfo=timezone.utc)
        for method in ("fts", "recent"):
            got = await HeadlineRetriever(pool, method, company_terms=terms).around(
                SYMBOL, d, as_of, HEADLINES_PER_DAY
            )
            got_ids = [new_to_old[h.id] for h in got]
            want = [c["id"] for c in sorted(
                (c for c in day["candidates"] if c[f"{method}_rank"] is not None),
                key=lambda c: c[f"{method}_rank"],
            )]
            if got_ids != want:
                mismatches.append(f"{day['date']} {method}: got {got_ids}, recorded {want}")
    assert not mismatches, "retrieval changed:\n" + "\n".join(mismatches[:5])
