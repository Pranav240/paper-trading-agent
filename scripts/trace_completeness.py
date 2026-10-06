r"""
Phase 10's pre-registered completeness check (docs/phase10-plan.md, s.4):
for every explanation in the given backtests, the citation check rebuilt
from the stored trace ALONE must reproduce the stored risk_explanations
row exactly. Must be 100%; prints every mismatch.

Run:
    $env:PYTHONPATH="."; .\papertrading\Scripts\python.exe scripts/trace_completeness.py <backtest_id> [...]
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import psycopg

from app.agent.trace_audit import completeness

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)


async def main() -> None:
    ids = [int(a) for a in sys.argv[1:]]
    if not ids:
        raise SystemExit("usage: trace_completeness.py <backtest_id> [...]")
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        cur = await conn.execute(
            "SELECT d.id FROM decisions d JOIN runs r ON r.id = d.run_id WHERE r.backtest_id = ANY(%s)", (ids,)
        )
        decision_ids = [row[0] for row in await cur.fetchall()]
        checked, mismatches = await completeness(conn, decision_ids)
    print(f"explanations checked: {checked}; rebuilt exactly from the trace: {checked - len(mismatches)}"
          f" ({(checked - len(mismatches)) / checked:.0%})" if checked else "no traced explanations")
    for m in mismatches:
        print("  MISMATCH", m)
    sys.exit(1 if mismatches or not checked else 0)


if __name__ == "__main__":
    asyncio.run(main())
