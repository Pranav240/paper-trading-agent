"""
CLI entry point for running a backtest (app/agent/backtest.py).

Usage (once historical_headlines is populated — see
docs/backtesting-plan.md and 004_historical_headlines.sql). Run from the
project root with PYTHONPATH=. so `app` and `tests` resolve as imports
(there's no setup.py/pyproject making this an installed package):

    PYTHONPATH=. python scripts/run_backtest.py \\
        --name "AAPL in-sample 2015-2021" \\
        --symbols AAPL \\
        --start 2015-01-01 --end 2021-12-31

Requires DATABASE_URL and ALPACA_API_KEY/ALPACA_SECRET_KEY in .env —
prices still come from the real Alpaca API even during a backtest (see
app/agent/data_sources.py's module docstring for why there's no separate
historical price source); only headlines are local. Does NOT require
OPENAI_API_KEY unless --use-real-llms is passed — by default this uses
the same FakeLLM test doubles the test suite uses, so you can smoke-test
the whole pipeline (does it run, does it persist correctly, are dates
sane) before spending any OpenAI budget on a run that might cover
thousands of simulated days.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from decimal import Decimal

from dotenv import load_dotenv
from psycopg_pool import AsyncConnectionPool

load_dotenv()

import os

# Windows defaults asyncio to ProactorEventLoop, which psycopg's async
# driver can't run under (confirmed live: "Psycopg cannot use the
# 'ProactorEventLoop' to run in async mode" on the first real Windows
# run of this script). FastAPI/uvicorn apparently sidesteps this on its
# own — app/main.py never hit it — but a plain `asyncio.run(main())`
# script like this one needs the selector loop policy set explicitly,
# before any event loop is created.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.agent.backtest import DEFAULT_SLIPPAGE_BPS, compute_backtest_metrics, run_backtest
from app.agent.data_sources import build_historical_data_sources
from app.agent.graph import build_decision_graph
from app.agent.state import TentativeDecision
from app.repository.backtest import BacktestRepository

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="Label for this backtest run.")
    parser.add_argument(
        "--symbols", required=True, nargs="+", help="One or more ticker symbols."
    )
    parser.add_argument(
        "--start", required=True, type=date.fromisoformat, help="YYYY-MM-DD"
    )
    parser.add_argument(
        "--end", required=True, type=date.fromisoformat, help="YYYY-MM-DD"
    )
    parser.add_argument(
        "--slippage-bps",
        type=Decimal,
        default=DEFAULT_SLIPPAGE_BPS,
        help=f"Default: {DEFAULT_SLIPPAGE_BPS}",
    )
    parser.add_argument(
        "--use-real-llms",
        action="store_true",
        help=(
            "Use real ChatOpenAI calls for the Sentiment Analyst and "
            "Portfolio Manager (needs OPENAI_API_KEY, costs real money, "
            "one call per node per symbol per simulated day). Without "
            "this flag, both nodes use a fixed HOLD test double instead "
            "— enough to verify the pipeline runs and persists correctly, "
            "not to evaluate the actual strategy."
        ),
    )
    return parser.parse_args()


def _fake_llms():
    """A fixed always-HOLD stand-in, imported from the test suite rather
    than redefined here — one definition of "what a fake LLM response
    looks like," not two that can drift apart. Only meant for a dry-run
    smoke test of the pipeline; --use-real-llms is what actually
    evaluates the strategy."""
    from app.agent.sentiment_analyst import SentimentCall
    from tests.agent_fakes import FakeLLM

    sentiment_llm = FakeLLM(
        SentimentCall(opinion="HOLD", confidence=0.5, reasoning="dry run — no real LLM")
    )
    portfolio_llm = FakeLLM(
        TentativeDecision(
            action="HOLD", quantity=0, confidence=0.5, reasoning="dry run — no real LLM"
        )
    )
    return sentiment_llm, portfolio_llm


async def main() -> None:
    args = _parse_args()

    pool = AsyncConnectionPool(DATABASE_URL, open=False)
    await pool.open(wait=True, timeout=10)

    price_source, headline_source = build_historical_data_sources(pool)

    sentiment_llm = portfolio_llm = None
    if not args.use_real_llms:
        sentiment_llm, portfolio_llm = _fake_llms()
        print(
            "NOTE: --use-real-llms not passed — running with fixed "
            "always-HOLD LLM stand-ins. This verifies the pipeline runs "
            "end-to-end, it does NOT evaluate the strategy. Pass "
            "--use-real-llms for a result worth reading."
        )

    def make_graph(backtest_id: int):
        return build_decision_graph(
            price_source=price_source,
            headline_source=headline_source,
            repository=BacktestRepository(pool, backtest_id),
            sentiment_llm=sentiment_llm,
            portfolio_llm=portfolio_llm,
        )

    try:
        backtest_id = await run_backtest(
            pool,
            make_graph,
            name=args.name,
            symbols=[s.upper() for s in args.symbols],
            window_start=args.start,
            window_end=args.end,
            slippage_bps=args.slippage_bps,
        )
        metrics = await compute_backtest_metrics(pool, backtest_id)
    finally:
        await pool.close()

    print(f"\nbacktest_id: {backtest_id}")
    for key, value in metrics.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    asyncio.run(main())
