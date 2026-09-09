"""
Export backtest data for the results dashboard, as UTF-8 JSON.

Replaces the `docker exec -i pta-postgres psql ... < export_dashboard_data.sql
> data/dashboard_data.json` dance, which had three recurring problems on
this machine, all documented the hard way:

- PowerShell has no `<` input redirection, so the documented command
  needed rewriting as `Get-Content file | docker exec -i ...` every time.
- PowerShell's `>` writes UTF-16, so every export had to be re-read with
  `encoding='utf-16'` and re-saved as UTF-8 before anything could parse
  it. That is why `_utf8` files exist all over `data/`.
- The round trip left mojibake (`ΓÇÖ` where an apostrophe should be) inside
  headline and reasoning text.

psycopg writes UTF-8 directly and none of the three can happen. `psql` is
not on PATH here anyway.

Run:
    python scripts/export_dashboard_data.py                 # default ids
    python scripts/export_dashboard_data.py 3 4 6 7 8 9 14
"""

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)
OUT_PATH = Path("data/dashboard_data.json")
DEFAULT_IDS = [3, 4, 6, 7, 8, 9, 14]

BACKTESTS = """
SELECT id, name, window_start, window_end, status
FROM backtests WHERE id = ANY(%s) ORDER BY id
"""

DAILY = """
SELECT r.backtest_id, r.as_of::date AS as_of,
       (d.technicals_snapshot->>'current_price')::numeric AS price,
       d.action, d.confidence
FROM decisions d JOIN runs r ON d.run_id = r.id
WHERE r.backtest_id = ANY(%s)
ORDER BY r.backtest_id, r.as_of
"""

TRADES = """
SELECT backtest_id, quantity, entry_price, exit_price,
       opened_at::date AS opened_at, closed_at::date AS closed_at,
       status, realized_pnl
FROM backtest_outcomes
WHERE backtest_id = ANY(%s)
ORDER BY backtest_id, opened_at
"""

# The sentiment agent's `opinion` is now a signed decimal string, so a
# plain GROUP BY would produce a row per distinct score instead of the
# handful of categories this chart wants. Numeric opinions are bucketed;
# categorical ones (every agent except post-rewrite sentiment) pass
# through unchanged.
OPINIONS = """
SELECT r.backtest_id, ao.agent_name,
       CASE
           WHEN ao.raw_output->>'score' IS NULL THEN ao.opinion
           WHEN (ao.raw_output->>'score')::float <= -0.3 THEN 'score <= -0.3'
           WHEN (ao.raw_output->>'score')::float <   0.0 THEN 'score -0.3..0'
           WHEN (ao.raw_output->>'score')::float =   0.0 THEN 'score 0.0'
           WHEN (ao.raw_output->>'score')::float <   0.3 THEN 'score 0..0.3'
           ELSE 'score >= 0.3'
       END AS opinion,
       COUNT(*) AS cnt
FROM agent_opinions ao
JOIN decisions d ON ao.decision_id = d.id
JOIN runs r ON d.run_id = r.id
WHERE r.backtest_id = ANY(%s)
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3
"""

# The raw per-day scores, for the distribution chart. Only post-rewrite
# runs have these; everything else returns nothing, which the dashboard
# renders as "this run predates the score node".
SCORES = """
SELECT r.backtest_id, r.as_of::date AS as_of,
       (ao.raw_output->>'score')::float AS score,
       ao.confidence
FROM agent_opinions ao
JOIN decisions d ON ao.decision_id = d.id
JOIN runs r ON d.run_id = r.id
WHERE r.backtest_id = ANY(%s)
  AND ao.agent_name = 'sentiment_analyst'
  AND ao.raw_output->>'score' IS NOT NULL
ORDER BY r.backtest_id, r.as_of
"""


def _default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"not JSON serializable: {type(obj)}")


def main() -> None:
    ids = [int(a) for a in sys.argv[1:]] or DEFAULT_IDS

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            payload = {}
            for key, query in (
                ("backtests", BACKTESTS),
                ("daily", DAILY),
                ("trades", TRADES),
                ("opinions", OPINIONS),
                ("scores", SCORES),
            ):
                cur.execute(query, (ids,))
                payload[key] = cur.fetchall()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(payload, default=_default, indent=1), encoding="utf-8"
    )

    print(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size:,} bytes)")
    for key, rows in payload.items():
        print(f"  {key:<10} {len(rows):>6} rows")


if __name__ == "__main__":
    main()
