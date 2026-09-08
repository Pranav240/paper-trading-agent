"""
Inventory of sentiment_analyst opinions across ALL backtests.

Purpose (Phase 04): the distilled model collapsed to always-HOLD because
the teacher (GPT-4o-mini) was itself ~96.8% HOLD, with only 12 BUY and 3
SELL examples in the 469-row export. This script is what answered "is
there more real BUY/SELL variety already in the DB?" — the answer was no,
which is what triggered the rewrite of the node to a continuous score.

It now reports BOTH opinion shapes, because `agent_opinions.opinion` is
free TEXT and the table holds both:

- LEGACY rows (backtests 3-9): 'BUY' / 'HOLD' / 'SELL'
- SCORE rows (everything after the 2026-09-08 rewrite): a signed decimal
  string like '+0.30', with the numeric value also in
  `raw_output->>'score'`

The score half is also the check docs/phase04-handoff.md requires before
any post-rewrite P&L number is trusted: if the scores cluster tightly at
0.0, the prompt rewrite failed the same way the categorical version did,
and the P&L comparison is not worth reading yet.

Run:  python scripts/inventory_sentiment.py
"""

import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

# `score IS NOT NULL` is the discriminator between the two eras rather
# than pattern-matching the opinion text: the numeric value is written to
# raw_output by the score node and was never written by the categorical
# one, so it's an exact test rather than a guess about string shape.
QUERY = """
SELECT
    r.backtest_id,
    COUNT(*)                                    AS total,
    COUNT(*) FILTER (WHERE ao.opinion = 'BUY')  AS buy,
    COUNT(*) FILTER (WHERE ao.opinion = 'SELL') AS sell,
    COUNT(*) FILTER (WHERE ao.opinion = 'HOLD') AS hold,
    COUNT(*) FILTER (
        WHERE ao.raw_output->>'score' IS NOT NULL
    )                                           AS scored,
    COUNT(DISTINCT d.symbol)                    AS symbols,
    MIN(r.as_of)::date                          AS first_day,
    MAX(r.as_of)::date                          AS last_day
FROM agent_opinions ao
JOIN decisions d ON ao.decision_id = d.id
JOIN runs r      ON d.run_id = r.id
WHERE ao.agent_name = 'sentiment_analyst'
  AND (ao.raw_output->>'n_headlines')::int > 0
GROUP BY r.backtest_id
ORDER BY r.backtest_id;
"""

SYMBOLS_QUERY = """
SELECT DISTINCT d.symbol
FROM agent_opinions ao
JOIN decisions d ON ao.decision_id = d.id
WHERE ao.agent_name = 'sentiment_analyst'
ORDER BY d.symbol;
"""

# The distribution check. A healthy score node produces spread: a low
# `pct_zero`, a stddev well clear of 0, and both signs represented. A
# degenerate one looks exactly like the old always-HOLD collapse — a
# stddev near zero and pct_zero near 100.
SCORE_STATS_QUERY = """
SELECT
    r.backtest_id,
    COUNT(*)                                              AS scored,
    ROUND(AVG(s.score)::numeric, 3)                       AS mean,
    ROUND(STDDEV_SAMP(s.score)::numeric, 3)               AS stddev,
    ROUND(MIN(s.score)::numeric, 2)                       AS min,
    ROUND(MAX(s.score)::numeric, 2)                       AS max,
    COUNT(*) FILTER (WHERE s.score > 0)                   AS pos,
    COUNT(*) FILTER (WHERE s.score < 0)                   AS neg,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE s.score = 0) / COUNT(*), 1
    )                                                     AS pct_zero
FROM agent_opinions ao
JOIN decisions d ON ao.decision_id = d.id
JOIN runs r      ON d.run_id = r.id
CROSS JOIN LATERAL (
    SELECT (ao.raw_output->>'score')::float AS score
) s
WHERE ao.agent_name = 'sentiment_analyst'
  AND ao.raw_output->>'score' IS NOT NULL
  AND (ao.raw_output->>'n_headlines')::int > 0
GROUP BY r.backtest_id
ORDER BY r.backtest_id;
"""

# Coarse histogram over the same rows, so "is it clustered at 0.0?" is
# answerable by eye and not only through a stddev.
SCORE_HISTOGRAM_QUERY = """
SELECT
    CASE
        WHEN s.score <= -0.5 THEN '[-1.0, -0.5]'
        WHEN s.score <  -0.1 THEN '(-0.5, -0.1)'
        WHEN s.score <   0.0 THEN '[-0.1,  0.0)'
        WHEN s.score =   0.0 THEN 'exactly 0.0'
        WHEN s.score <=  0.1 THEN '( 0.0,  0.1]'
        WHEN s.score <   0.5 THEN '( 0.1,  0.5)'
        ELSE                      '[ 0.5,  1.0]'
    END AS bucket,
    COUNT(*) AS n
FROM agent_opinions ao
CROSS JOIN LATERAL (
    SELECT (ao.raw_output->>'score')::float AS score
) s
WHERE ao.agent_name = 'sentiment_analyst'
  AND ao.raw_output->>'score' IS NOT NULL
  AND (ao.raw_output->>'n_headlines')::int > 0
GROUP BY bucket
ORDER BY MIN(s.score);
"""


def _print_table(cur) -> list[tuple]:
    rows = cur.fetchall()
    headers = [c.name for c in cur.description]
    width = 13 * len(headers)

    print(" | ".join(f"{h:>10}" for h in headers))
    print("-" * width)
    for row in rows:
        print(" | ".join(f"{str(v):>10}" for v in row))
    print("-" * width)
    return rows


def main() -> None:
    dsn = os.environ["DATABASE_URL"]
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            print("=== opinions per backtest (headline-bearing rows only) ===")
            cur.execute(QUERY)
            rows = _print_table(cur)

            totals = [0, 0, 0, 0, 0]
            for row in rows:
                for i in range(5):  # total, buy, sell, hold, scored
                    totals[i] += row[1 + i]

            print(
                f"ALL BACKTESTS: total={totals[0]}  BUY={totals[1]}  "
                f"SELL={totals[2]}  HOLD={totals[3]}  scored={totals[4]}"
            )
            legacy = totals[1] + totals[2] + totals[3]
            if legacy and totals[3]:
                print(
                    f"  legacy categorical rows: {legacy}, "
                    f"always-HOLD baseline = {100.0 * totals[3] / legacy:.1f}%"
                )

            cur.execute(SYMBOLS_QUERY)
            symbols = [r[0] for r in cur.fetchall()]
            print(f"distinct symbols: {len(symbols)} -> {symbols}")

            if not totals[4]:
                print(
                    "\nNo score-shaped rows yet — every sentiment row in the "
                    "DB predates the 2026-09-08 continuous-score rewrite."
                )
                return

            print("\n=== score distribution per backtest ===")
            cur.execute(SCORE_STATS_QUERY)
            _print_table(cur)

            print("\n=== score histogram (all score-shaped rows) ===")
            cur.execute(SCORE_HISTOGRAM_QUERY)
            hist = cur.fetchall()
            total = sum(n for _, n in hist) or 1
            for bucket, n in hist:
                bar = "#" * round(40 * n / total)
                print(f"  {bucket:>13} {n:>5} {bar}")
            print(
                "\nRead this before trusting any P&L number: a healthy score "
                "node spreads across buckets. If nearly everything lands in "
                "'exactly 0.0', the prompt rewrite failed the same way the "
                "categorical node did — fix that before comparing results."
            )


if __name__ == "__main__":
    main()
