"""
Cheap pre-flight check on the rewritten sentiment node's score spread.

Why this exists: docs/phase04-handoff.md requires checking the score
distribution BEFORE trusting any P&L number from the re-ablation — if the
scores cluster at 0.0, the prompt rewrite failed the same way the
categorical version did, and the two full backtest runs it takes to
measure P&L would be wasted. This script answers that question for a few
cents instead, by running the real sentiment node over a sample of real
headline days and printing the distribution.

What it does NOT do: touch prices, run the graph, or write anything. It
reads `historical_headlines` and calls GPT-4o-mini, nothing else. So it
needs DATABASE_URL and OPENAI_API_KEY, but not Alpaca keys.

Run (from the project root):

    $env:PYTHONPATH="."; python scripts/probe_sentiment_scores.py \
        --symbol AAPL --start 2022-06-03 --end 2023-06-30 --n 25

Cost: one gpt-4o-mini call per sampled day (`--n`, default 25). That is a
fraction of a cent — deliberately, so this is always worth running first.

Reading the output:

- GOOD: scores spread across several buckets, stddev comfortably above
  0, both signs present, few exact zeros.
- BAD: nearly everything at exactly 0.0. That is the same collapse the
  BUY/HOLD/SELL node had, wearing different clothes. Fix the prompt
  before spending anything on backtest runs.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
from datetime import date, datetime, time, timezone

from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

load_dotenv()

# Same Windows event-loop fix as scripts/run_backtest.py and
# tests/conftest.py — psycopg's async driver can't run on the default
# ProactorEventLoop. Must happen before any loop is created.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.agent.data_sources import HistoricalHeadlineSource
from app.agent.sentiment_analyst import make_sentiment_analyst_node

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)

DAYS_QUERY = """
SELECT DISTINCT published_at::date AS day
FROM historical_headlines
WHERE symbol = %s
  AND published_at >= %s
  AND published_at < %s
ORDER BY day
"""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2022, 6, 3))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2023, 6, 30))
    parser.add_argument(
        "--n",
        type=int,
        default=25,
        help="How many days to sample (= how many LLM calls). Default 25.",
    )
    return parser.parse_args()


def _evenly_spaced(items: list, n: int) -> list:
    """Sample across the whole window rather than taking the first n days
    — a contiguous block of days could all sit inside one news cycle and
    make the spread look narrower (or wider) than it really is."""
    if n >= len(items):
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


async def main() -> None:
    args = _parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set — this probe makes real LLM calls.")

    pool = AsyncConnectionPool(DATABASE_URL, open=False)
    await pool.open(wait=True, timeout=10)

    try:
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    DAYS_QUERY, (args.symbol, args.start, args.end)
                )
                days = [r["day"] for r in await cur.fetchall()]

        if not days:
            raise SystemExit(
                f"No headlines for {args.symbol} between {args.start} and "
                f"{args.end} — check coverage before running this."
            )

        sample = _evenly_spaced(days, args.n)
        print(
            f"{len(days)} headline-days available for {args.symbol}; "
            f"probing {len(sample)} of them with real gpt-4o-mini calls.\n"
        )

        node = make_sentiment_analyst_node(HistoricalHeadlineSource(pool))

        results: list[tuple[date, float, float | None, int, str]] = []
        for day in sample:
            # as_of is end-of-day UTC so the node's 3-day lookback sees
            # that day's headlines, matching what a backtest would see.
            as_of = datetime.combine(day, time(23, 59), tzinfo=timezone.utc)
            out = await node({"symbol": args.symbol, "as_of": as_of})
            op = out["sentiment_opinion"]
            results.append(
                (
                    day,
                    float(op.raw_output["score"]),
                    op.confidence,
                    op.raw_output["n_headlines"],
                    op.reasoning,
                )
            )
            print(
                f"  {day}  score={op.opinion}  conf={op.confidence}  "
                f"n_headlines={op.raw_output['n_headlines']}"
            )
    finally:
        await pool.close()

    scored = [r for r in results if r[3] > 0]
    if not scored:
        raise SystemExit("\nEvery sampled day had zero headlines — nothing to judge.")

    scores = [r[1] for r in scored]
    zeros = sum(1 for s in scores if s == 0.0)
    pos = sum(1 for s in scores if s > 0)
    neg = sum(1 for s in scores if s < 0)
    stdev = statistics.stdev(scores) if len(scores) > 1 else 0.0

    print(f"\n=== {len(scores)} scored days ({len(results) - len(scored)} had no headlines) ===")
    print(f"  mean      {statistics.fmean(scores):+.3f}")
    print(f"  stddev    {stdev:.3f}")
    print(f"  min/max   {min(scores):+.2f} / {max(scores):+.2f}")
    print(f"  positive  {pos}   negative {neg}   exactly 0.0  {zeros} "
          f"({100.0 * zeros / len(scores):.1f}%)")
    print(f"  distinct values: {len(set(scores))}")

    # Confidence gets its own degeneracy check because the FIRST version
    # of this probe reported "USABLE SPREAD" on a run where the model
    # returned confidence=0.8 on every single day. The scores were fine;
    # the confidence field was a constant carrying no information — the
    # same thing the PhraseBank adapter's confidence scores did, which is
    # what made thresholding a dead end there. A constant doesn't break
    # the ablation (the Portfolio Manager is told the two axes are
    # separate), but it does mean confidence is not worth weighting, and
    # a fine-tune should not treat it as a second regression target.
    confidences = [r[2] for r in scored if r[2] is not None]
    if confidences:
        distinct_conf = sorted(set(confidences))
        c_stdev = statistics.stdev(confidences) if len(confidences) > 1 else 0.0
        print(
            f"  confidence: {len(distinct_conf)} distinct value(s), "
            f"stddev {c_stdev:.3f}, range {min(confidences):.2f}"
            f"-{max(confidences):.2f}"
        )
        if len(distinct_conf) == 1:
            print(
                f"  ^ CONSTANT at {distinct_conf[0]:.2f} — that field carries no "
                "information on this sample. Don't weight it, don't train on it."
            )

    print("\n=== histogram ===")
    buckets = [
        ("[-1.0, -0.5]", lambda s: s <= -0.5),
        ("(-0.5, -0.1)", lambda s: -0.5 < s < -0.1),
        ("[-0.1,  0.0)", lambda s: -0.1 <= s < 0.0),
        ("exactly 0.0 ", lambda s: s == 0.0),
        ("( 0.0,  0.1]", lambda s: 0.0 < s <= 0.1),
        ("( 0.1,  0.5)", lambda s: 0.1 < s < 0.5),
        ("[ 0.5,  1.0]", lambda s: s >= 0.5),
    ]
    for label, pred in buckets:
        n = sum(1 for s in scores if pred(s))
        print(f"  {label} {n:>4} {'#' * round(40 * n / len(scores))}")

    # A deliberately blunt verdict, in the spirit of design decision 11:
    # state the baseline comparison rather than letting a nice-looking
    # number speak for itself.
    print()
    if zeros / len(scores) > 0.6 or stdev < 0.05:
        print(
            "VERDICT: COLLAPSED. The scores are clustered at/near zero — this "
            "is the same degenerate behaviour the BUY/HOLD/SELL node had. Fix "
            "SYSTEM_PROMPT before spending anything on backtest runs."
        )
    elif pos == 0 or neg == 0:
        print(
            "VERDICT: ONE-SIDED. There is spread, but only in one direction. "
            "That is the PhraseBank adapter's failure mode ('stock-adjacent "
            "headline -> lean bullish'). Worth investigating before running "
            "the ablation."
        )
    else:
        print(
            "VERDICT: USABLE SPREAD. Both signs present and the scores vary. "
            "Proceed to the re-ablation (two runs per configuration) — and "
            "re-check the distribution on the full run with "
            "scripts/inventory_sentiment.py, since 25 days is a small sample."
        )
        if max(abs(s) for s in scores) < 0.5:
            print(
                f"  Caveat: nothing exceeded |{max(abs(s) for s in scores):.2f}| — "
                "the model is using a narrow band and never calls anything "
                "strongly. Enough variance to trade on and to train on, but "
                "note it before reading the magnitudes as conviction."
            )


if __name__ == "__main__":
    asyncio.run(main())
