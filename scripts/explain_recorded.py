"""
Run the phase 08 explainer, for real, on decisions a finished backtest
already recorded -- without re-running the backtest. Only the explainer's
LLM calls are paid for.

Each decision's state is rebuilt from what the backtest stored: the
Portfolio Manager's proposal, the risk manager's verdict and raw_output,
the final action. The VaR's worst-loss days are recomputed from prices
with the same tail_losses the risk node uses (backtests run before phase
08 didn't record them). Explanations are written to risk_explanations
against the original decision ids.

PRE-REGISTERED SAMPLE (committed before the first run)
------------------------------------------------------
- Decisions: those where the VaR budget changed the trade
  (var_budget_scale / var_budget_veto), in date order; N evenly spaced
  ones per backtest (index round(i * count / N)), no other selection.
- First run: 10 from backtest 45 (in-sample, half its driver days have no
  news) and 10 from backtest 71 (out-of-sample, full coverage), retrieval
  "fts", Claude Haiku 4.5, spending cap $0.15 for the whole run.
- Reported: share of explanations whose citations all hold up (the
  pre-registered quality check), citations per explanation, how many
  driver days with no headlines were left uncited, tokens and cost.
  Whether explanations beat the template is phase 09's question, not
  this run's.

Run:
    $env:PYTHONPATH="."; .\\papertrading\\Scripts\\python.exe scripts/explain_recorded.py 45 71 --per-backtest 10 --max-cost-usd 0.15
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from langchain_core.callbacks import UsageMetadataCallbackHandler
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.agent.backtest import usage_summary
from app.agent.data_sources import NEW_YORK, AlpacaPriceSource
from app.agent.explainer import (
    TRIGGER_FLAGS,
    HeadlineRetriever,
    make_explainer_node,
    record_explanation,
)
from app.agent.llm import default_llm, describe, usage_cost_usd
from app.agent.risk_math import simple_returns, tail_losses
from app.agent.state import AgentOpinion, RiskVerdict, TentativeDecision
from app.agent.trace import TraceRecorder, record_trace

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)

DECISIONS = """
SELECT d.id AS decision_id, d.symbol, r.as_of, d.action AS final_action,
       pm.opinion AS pm_action, (pm.raw_output->>'quantity')::int AS pm_qty,
       pm.confidence AS pm_conf, pm.reasoning AS pm_reasoning,
       rm.opinion AS risk_opinion, rm.reasoning AS risk_reasoning,
       rm.raw_output AS risk_raw
FROM var_forecasts vf
JOIN decisions d ON d.id = vf.decision_id
JOIN runs r ON r.id = d.run_id
JOIN agent_opinions pm ON pm.decision_id = d.id AND pm.agent_name = 'portfolio_manager'
JOIN agent_opinions rm ON rm.decision_id = d.id AND rm.agent_name = 'risk_manager'
WHERE vf.backtest_id = %s AND vf.risk_flags && %s
  AND NOT EXISTS (SELECT 1 FROM risk_explanations e WHERE e.decision_id = d.id)
ORDER BY r.as_of
"""


class _CapReached(Exception):
    """The spending cap stopped the run (reported, not an error)."""


def evenly_spaced(rows: list, n: int) -> list:
    if n >= len(rows):
        return rows
    return [rows[round(i * len(rows) / n)] for i in range(n)]


def final_quantity(row: dict) -> int:
    raw = row["risk_raw"]
    if row["risk_opinion"] == "VETO":
        return 0
    if "var_budget_scale" in raw.get("risk_flags", []):
        return raw["var_max_qty"] - raw["current_qty"]
    return row["pm_qty"]


async def var_tail_for(source, symbol: str, rows: list[dict]) -> dict[int, list[dict]]:
    """decision_id -> the VaR's worst-loss days as the risk node saw them."""
    first, last = rows[0]["as_of"], rows[-1]["as_of"]
    bars = await source.get_recent_bars(
        symbol, last + timedelta(days=1), lookback_days=(last - first).days + 420
    )
    days = [b.timestamp.astimezone(NEW_YORK).date() for b in bars]
    closes = [b.close for b in bars]
    out = {}
    for row in rows:
        n = sum(1 for d in days if d < row["as_of"].astimezone(NEW_YORK).date())
        tail = tail_losses(simple_returns(closes[:n]), days[1:n]) or []
        out[row["decision_id"]] = [{"date": str(d), "return": r} for d, r in tail]
    return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("backtest_ids", type=int, nargs="+")
    parser.add_argument("--per-backtest", type=int, default=10)
    parser.add_argument("--method", choices=["fts", "recent"], default="fts")
    parser.add_argument("--max-cost-usd", type=float, required=True)
    parser.add_argument(
        "--trace", action="store_true",
        help="Phase 10: store the explainer's reasoning trace with each explanation. "
             "These decisions predate tracing, so their traces hold only the explainer's steps.",
    )
    args = parser.parse_args()

    pool = AsyncConnectionPool(DATABASE_URL, open=False)
    await pool.open(wait=True, timeout=10)
    source = AlpacaPriceSource(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
    retriever = HeadlineRetriever(pool, method=args.method)
    node = make_explainer_node(retriever, llm=default_llm("explainer"))
    usage = UsageMetadataCallbackHandler()
    print(f"explainer: {describe()['explainer']}, retrieval {args.method}, cap ${args.max_cost_usd:.2f}\n")

    results, done = [], 0
    try:
        for backtest_id in args.backtest_ids:
            async with pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(DECISIONS, (backtest_id, list(TRIGGER_FLAGS)))
                    rows = evenly_spaced(await cur.fetchall(), args.per_backtest)
            if not rows:
                continue
            tails = await var_tail_for(source, rows[0]["symbol"], rows)

            for row in rows:
                spent = usage_cost_usd(usage_summary(usage))
                if done and spent + spent / done > args.max_cost_usd:
                    print(f"Stopping at the cap: ${spent:.4f} spent over {done} calls.")
                    raise _CapReached
                raw = {**row["risk_raw"], "var_tail": tails[row["decision_id"]]}
                state = {
                    "symbol": row["symbol"],
                    "as_of": row["as_of"],
                    "tentative_decision": TentativeDecision(
                        action=row["pm_action"], quantity=row["pm_qty"] or 0,
                        confidence=row["pm_conf"] or 0.5, reasoning=row["pm_reasoning"],
                    ),
                    "risk_verdict": RiskVerdict(opinion=row["risk_opinion"], reasoning=row["risk_reasoning"]),
                    "risk_opinion": AgentOpinion(
                        agent_name="risk_manager", opinion=row["risk_opinion"],
                        reasoning=row["risk_reasoning"], raw_output=raw,
                    ),
                    "final_action": row["final_action"],
                    "final_quantity": final_quantity(row),
                }
                recorder = TraceRecorder() if args.trace else None
                out = await _invoke(node, state, usage, recorder)
                explanation = out["risk_explanation"]
                done += 1
                if explanation is None:
                    continue
                async with pool.connection() as conn:
                    async with conn.transaction():  # explanation and its trace together
                        await record_explanation(
                            conn, decision_id=row["decision_id"], symbol=row["symbol"],
                            as_of=row["as_of"], explanation=explanation,
                        )
                        if recorder is not None:
                            await record_trace(conn, decision_id=row["decision_id"], recorder=recorder)
                results.append((backtest_id, row, explanation))
    except _CapReached:
        pass
    finally:
        await pool.close()

    report(results, usage)


async def _invoke(node, state, usage, recorder=None):
    """Call the node as a runnable so the usage callback (and the trace
    recorder, if any) see its LLM call."""
    from langchain_core.runnables import RunnableLambda

    if recorder is None:
        return await RunnableLambda(node).ainvoke(state, config={"callbacks": [usage]})
    token = recorder.activate()
    try:
        return await RunnableLambda(node, name="explainer").ainvoke(
            state, config={"callbacks": [usage, recorder], "metadata": {"langgraph_node": "explainer"}}
        )
    finally:
        TraceRecorder.deactivate(token)


def report(results, usage) -> None:
    n = len(results)
    if not n:
        print("No explanations written.")
        return
    valid = sum(e.citations_valid for _, _, e in results)
    no_news = no_news_uncited = 0
    for _, _, e in results:
        for d in e.drivers:
            if e.retrieved.get(d["date"]) == []:
                no_news += 1
                no_news_uncited += not d["headline_ids"]
    summary = usage_summary(usage)
    print(f"explanations written: {n}")
    print(f"  all citations grounded: {valid}/{n} ({valid / n:.0%})")
    print(f"  citations per explanation: {sum(len(e.cited_headline_ids) for _, _, e in results) / n:.1f}")
    print(f"  driver days with no headlines left uncited: {no_news_uncited}/{no_news}")
    for _, _, e in results:
        for p in e.validation_problems:
            print(f"    problem: {p}")
    print(f"  tokens: {summary}")
    print(f"  cost: ${usage_cost_usd(summary):.4f}\n")
    for backtest_id, row, e in results[:1] + results[-1:]:
        print(f"--- backtest {backtest_id}, {row['as_of'].date()} ({row['risk_opinion']}) ---")
        print(f"LLM:      {e.summary}")
        print(f"template: {e.template_baseline}\n")


if __name__ == "__main__":
    asyncio.run(main())
