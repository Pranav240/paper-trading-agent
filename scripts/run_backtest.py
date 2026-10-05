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
import subprocess
import sys
from datetime import date
from decimal import Decimal

import psycopg
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
from app.agent.data_sources import (
    OpenPriceBook,
    alpaca_trading_days,
    build_historical_data_sources,
)
from app.agent.graph import build_decision_graph
from app.agent.llm import describe as describe_llms
from app.agent.risk_manager import MAX_POSITION_QTY, VAR_BUDGET
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
            "Use real LLM calls for the Sentiment Analyst and Portfolio "
            "Manager, from LLM_PROVIDER (app/agent/llm.py; needs "
            "OPENAI_API_KEY or ANTHROPIC_API_KEY, costs real money, "
            "one call per node per symbol per simulated day). Without "
            "this flag, both nodes use fixed neutral test doubles instead "
            "— enough to verify the pipeline runs and persists correctly, "
            "not to evaluate the actual strategy."
        ),
    )
    parser.add_argument(
        "--no-sentiment",
        action="store_true",
        help=(
            "ABLATION: force the Sentiment Analyst to a fixed neutral "
            "score (0.0) stand-in while leaving the Portfolio Manager on "
            "real LLM calls. Combine with --use-real-llms. Answers 'is the "
            "sentiment node's actual content worth anything?' by holding "
            "the graph structure constant and removing only the signal. "
            "Compare the resulting metrics against the equivalent full "
            "run over the same window."
        ),
    )
    parser.add_argument(
        "--sentiment-mode",
        choices=["score", "categorical"],
        default="score",
        help=(
            "'score' (default): the current -1..+1 sentiment score. "
            "'categorical': V1's BUY/SELL/HOLD vote and the Portfolio "
            "Manager prompt written for it, verbatim "
            "(app/agent/v1_categorical.py) -- only for rerunning V1 "
            "backtests #3, #4, #6, #9."
        ),
    )
    parser.add_argument(
        "--resume",
        type=int,
        metavar="BACKTEST_ID",
        help=(
            "Continue an interrupted backtest after its last completed day "
            "instead of starting a new one. Window and settings must match "
            "the original (checked); the resume is recorded in its config."
        ),
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        help=(
            "Spending cap for this run's LLM calls, in USD. The run stops "
            "before a day that would likely cross it (marked FAILED, "
            "resumable with --resume). Required with --use-real-llms."
        ),
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help=(
            "Allow --use-real-llms with uncommitted changes. Off by default: "
            "a paid run records its git commit in backtests.config, and that "
            "only identifies the code if the tree is clean."
        ),
    )
    return parser.parse_args()


def _git_state() -> tuple[str, bool]:
    """(HEAD commit, whether tracked files have uncommitted changes)."""
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    )
    return commit, dirty


def _fake_llms(sentiment_mode: str = "score"):
    """Fixed neutral stand-ins (sentiment score 0.00, or a HOLD vote in
    categorical mode as V1's dry run used; Portfolio Manager action HOLD),
    imported from the test suite rather than redefined
    here — one definition of "what a fake LLM response looks like," not
    two that can drift apart. Only meant for a dry-run
    smoke test of the pipeline; --use-real-llms is what actually
    evaluates the strategy."""
    from app.agent.sentiment_analyst import SentimentScore
    from app.agent.v1_categorical import SentimentCall
    from tests.agent_fakes import FakeLLM

    if sentiment_mode == "categorical":
        sentiment_llm = FakeLLM(
            SentimentCall(opinion="HOLD", confidence=0.5, reasoning="dry run — no real LLM")
        )
    else:
        sentiment_llm = FakeLLM(
            SentimentScore(score=0.0, confidence=0.5, reasoning="dry run — no real LLM")
        )
    portfolio_llm = FakeLLM(
        TentativeDecision(
            action="HOLD", quantity=0, confidence=0.5, reasoning="dry run — no real LLM"
        )
    )
    return sentiment_llm, portfolio_llm


async def main() -> None:
    args = _parse_args()

    if args.use_real_llms and args.max_cost_usd is None:
        raise SystemExit("--use-real-llms needs --max-cost-usd: paid runs must have a spending cap.")

    commit, dirty = _git_state()
    if args.use_real_llms and dirty and not args.allow_dirty:
        raise SystemExit(
            "Uncommitted changes to tracked files. Commit first so this paid "
            "run's recorded git commit identifies the code (or --allow-dirty)."
        )

    pool = AsyncConnectionPool(DATABASE_URL, open=False)
    await pool.open(wait=True, timeout=10)

    price_source, headline_source = build_historical_data_sources(pool)

    sentiment_llm = portfolio_llm = None
    if not args.use_real_llms:
        sentiment_llm, portfolio_llm = _fake_llms(args.sentiment_mode)
        print(
            "NOTE: --use-real-llms not passed — running with fixed "
            "neutral LLM stand-ins. This verifies the pipeline runs "
            "end-to-end, it does NOT evaluate the strategy. Pass "
            "--use-real-llms for a result worth reading."
        )

    if args.no_sentiment:
        sentiment_llm, _ = _fake_llms(args.sentiment_mode)
        print(
            "ABLATION MODE: sentiment node forced to a neutral score of "
            "0.00 (confidence 0.5). The node still runs and the Portfolio "
            "Manager still sees its score — only the content is removed. "
            "Compare against the equivalent full run over the same window."
        )

    def make_graph(backtest_id: int):
        return build_decision_graph(
            price_source=price_source,
            headline_source=headline_source,
            repository=BacktestRepository(pool, backtest_id),
            sentiment_llm=sentiment_llm,
            portfolio_llm=portfolio_llm,
            sentiment_mode=args.sentiment_mode,
        )

    trading_days = alpaca_trading_days(args.start, args.end)
    real = args.use_real_llms
    llms = describe_llms()
    # Everything needed to say exactly how this result was produced.
    config = {
        "symbols": [s.upper() for s in args.symbols],
        # Provider and models from LLM_PROVIDER / *_MODEL env (app/agent/llm.py).
        "llm": describe_llms(),
        "models": {
            "portfolio_manager": llms["portfolio_manager"]["model"] if real else "fake:HOLD",
            "sentiment_analyst": (
                "fake:neutral"
                if (args.no_sentiment or not real)
                else llms["sentiment_analyst"]["model"]
            ),
        },
        "sentiment_mode": args.sentiment_mode,
        "no_sentiment_ablation": args.no_sentiment,
        "risk_node": {"max_position_qty": MAX_POSITION_QTY, "var_budget": VAR_BUDGET},
        "slippage_bps": str(args.slippage_bps),
        "fill": "decision-day open + slippage",
        "calendar": "alpaca market calendar",
        "trading_days": len(trading_days),
        "git_commit": commit,
        "git_dirty": dirty,
    }

    try:
        backtest_id = await run_backtest(
            pool,
            make_graph,
            name=args.name,
            symbols=[s.upper() for s in args.symbols],
            window_start=args.start,
            window_end=args.end,
            slippage_bps=args.slippage_bps,
            config=config,
            trading_days=trading_days,
            execution_prices=OpenPriceBook(price_source, args.start, args.end),
            resume_backtest_id=args.resume,
            max_cost_usd=args.max_cost_usd,
        )
        metrics = await compute_backtest_metrics(pool, backtest_id)
    finally:
        await pool.close()

    print(f"\nbacktest_id: {backtest_id}")
    for key, value in metrics.items():
        print(f"  {key}: {value}")

    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        cur = await conn.execute("SELECT llm_usage FROM backtests WHERE id = %s", (backtest_id,))
        print(f"  llm_usage: {(await cur.fetchone())[0]}")


if __name__ == "__main__":
    asyncio.run(main())
