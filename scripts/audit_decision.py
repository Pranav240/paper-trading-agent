"""
Audit one decision from its stored trace (phase 10): inputs -> retrieval
-> prompt -> response -> check -> final action, read back from
`trace_steps` alone. Also runs the completeness check for that decision
if it has an explanation.

Run:
    $env:PYTHONPATH="."; .\\papertrading\\Scripts\\python.exe scripts/audit_decision.py <decision_id> [--full]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import psycopg
from psycopg.rows import dict_row

from app.agent.trace_audit import completeness, format_audit, load_steps

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("decision_id", type=int)
    parser.add_argument("--full", action="store_true", help="print prompts and results in full")
    args = parser.parse_args()
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT d.id, d.symbol, d.action, d.reasoning, d.run_id, r.as_of "
                "FROM decisions d JOIN runs r ON r.id = d.run_id WHERE d.id = %s",
                (args.decision_id,),
            )
            decision = await cur.fetchone()
        if decision is None:
            raise SystemExit(f"No decision {args.decision_id}.")
        steps = await load_steps(conn, args.decision_id)
        if not steps:
            raise SystemExit(f"Decision {args.decision_id} has no trace (made before phase 10?).")
        print(format_audit(decision, steps, full=args.full))
        checked, mismatches = await completeness(conn, [args.decision_id])
        if checked:
            print("\ncompleteness:", "explanation rebuilt from the trace matches the stored row"
                  if not mismatches else "MISMATCH\n  " + "\n  ".join(mismatches))


if __name__ == "__main__":
    asyncio.run(main())
