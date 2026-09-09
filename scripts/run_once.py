"""
Run one live decision cycle and exit. The entry point for the scheduled
deployment (Phase 06).

Why this exists rather than `curl -X POST localhost:8000/run/trigger`: the
scheduled run is a batch job, and making it depend on a long-running web
server means the daily trade decision silently stops happening whenever
uvicorn is down for a reason nobody noticed. A batch job should be a
process that starts, does the work, exits with a status code, and can be
watched by whatever started it. It also means the deployment does not have
to expose an HTTP port at all.

The work itself is not reimplemented — this calls exactly the same
`run_decision_cycle` the API route calls, through the same
`PostgresRepository`, so there is one code path for a live cycle and no
chance of the scheduled version drifting from the interactive one.

Requires DATABASE_URL, ALPACA_API_KEY / ALPACA_SECRET_KEY and
OPENAI_API_KEY. Costs real OpenAI money on every invocation: two LLM calls
per watchlist symbol.

Run:
    PYTHONPATH=. python scripts/run_once.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv
from psycopg_pool import AsyncConnectionPool

load_dotenv()

# Same Windows event-loop fix as scripts/run_backtest.py and
# tests/conftest.py. Irrelevant on the EC2 instance this normally runs on,
# kept so the script is testable on the developer's machine before deploying
# — which is the only way anyone finds out it works.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.repository.postgres import PostgresRepository

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)


async def main() -> int:
    pool = AsyncConnectionPool(DATABASE_URL, open=False)
    await pool.open(wait=True, timeout=15)

    try:
        result = await PostgresRepository(pool).trigger_run()
    finally:
        await pool.close()

    print(f"run_id      {result.run_id}")
    print(f"status      {result.status}")
    print(f"decisions   {len(result.decisions)}")
    for d in result.decisions:
        print(f"  {d.symbol:<6} {d.action:<5} conf={d.confidence}")

    # A non-zero exit is what makes a failed run visible to whatever invoked
    # it — SSM RunCommand marks the invocation Failed, which is the signal a
    # CloudWatch alarm or a glance at the console can actually act on. A
    # batch job that always exits 0 is a batch job nobody can monitor.
    return 0 if result.status == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
