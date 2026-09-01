"""
One-time ETL: import the FNSPID headline dataset's CSV into
`historical_headlines` (see db/migrations/004_historical_headlines.sql
and docs/backtesting-plan.md for why this table exists).

Run this on YOUR machine, not in a cloud sandbox — the file is ~23GB
(15.7M rows across the full dataset), far too large to stage anywhere,
and this needs to reach your real local Postgres. From the project root,
with your venv active:

    PYTHONPATH=. python scripts/import_fnspid.py --symbols AAPL

Requires `pandas` — the venv this project has been using so far was
installed from an unpinned package list that didn't include it (see
README/project notes on the Python 3.10-vs-3.11 mismatch). Install it
first if needed: `pip install pandas`.

Why streaming (pandas `chunksize`), not `pd.read_csv` in one shot: a
23GB file loaded whole would need far more RAM than a normal dev machine
has. Reading in chunks (default 200k rows) keeps memory bounded
regardless of file size — each chunk is filtered down to a handful of
rows (one symbol, one date range) and only THOSE get held in memory or
sent to Postgres.

Column mapping, confirmed directly from the CSV's header (NOT assumed):
    Date          -> published_at   ("2023-12-16 23:00:00 UTC" format,
                                      parsed as UTC)
    Article_title -> headline
    Stock_symbol  -> symbol
Every other column (Url, Publisher, Author, Article, and four different
auto-generated summaries) is dropped on read (`usecols`) — this project
only needs "what headline, for what symbol, at what time," matching
Headline/AlpacaHeadlineSource's existing shape. The full article text
and summaries were never part of the plan (docs/backtesting-plan.md asks
for "time-aligned financial news records," not article bodies), and
keeping them out of `usecols` is also what makes a 23GB file tractable
to stream at all.

Default symbol filter is your live watchlist (`SELECT symbol FROM
watchlist`) — not the full 4,775-company dataset — so the first run
imports only what you'll actually backtest. Pass --symbols to override
for symbols not on your watchlist yet (a backtest is explicitly allowed
to cover those — see 005_backtest_outcomes.sql's comment on why that
table has no watchlist FK).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date

import pandas as pd
import psycopg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)

CHUNK_ROWS = 200_000
INSERT_BATCH = 5_000

USE_COLS = ["Date", "Article_title", "Stock_symbol"]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv-path",
        default="data/nasdaq_exteral_data.csv",
        help="Path to the FNSPID CSV (default: data/nasdaq_exteral_data.csv).",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help=(
            "Symbols to import (space-separated). Default: read from your "
            "watchlist table."
        ),
    )
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        default=date(2015, 1, 1),
        help="Earliest published_at to import, YYYY-MM-DD (default 2015-01-01, "
        "matching the backtesting plan's in-sample window start).",
    )
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=date(2023, 12, 31),
        help="Latest published_at to import, YYYY-MM-DD (default 2023-12-31).",
    )
    parser.add_argument(
        "--source-label",
        default="fnspid",
        help="Value stored in historical_headlines.source (default: fnspid).",
    )
    return parser.parse_args()


def _resolve_symbols(conn: psycopg.Connection, explicit: list[str] | None) -> set[str]:
    if explicit:
        return {s.upper() for s in explicit}
    rows = conn.execute("SELECT symbol FROM watchlist").fetchall()
    symbols = {row[0] for row in rows}
    if not symbols:
        print(
            "No symbols in your watchlist and none passed via --symbols — "
            "nothing to import.",
            file=sys.stderr,
        )
        sys.exit(1)
    return symbols


def main() -> None:
    args = _parse_args()

    if not os.path.exists(args.csv_path):
        print(f"CSV not found at {args.csv_path!r}.", file=sys.stderr)
        sys.exit(1)

    conn = psycopg.connect(DATABASE_URL, autocommit=False)
    symbols = _resolve_symbols(conn, args.symbols)
    print(f"Importing headlines for: {sorted(symbols)}")
    print(f"Date range: {args.start} .. {args.end}")

    start_ts = pd.Timestamp(args.start, tz="UTC")
    # End-of-day on the end date, so --end 2023-12-31 actually includes
    # articles published during that day rather than excluding all of it.
    end_ts = pd.Timestamp(args.end, tz="UTC") + pd.Timedelta(days=1)

    reader = pd.read_csv(
        args.csv_path,
        usecols=USE_COLS,
        dtype=str,
        chunksize=CHUNK_ROWS,
        on_bad_lines="warn",
        encoding="utf-8",
    )

    total_rows_scanned = 0
    total_rows_matched = 0
    total_rows_inserted = 0
    t0 = time.time()

    insert_sql = """
        INSERT INTO historical_headlines (symbol, published_at, headline, source)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (symbol, published_at, headline) DO NOTHING
    """

    try:
        for chunk_num, chunk in enumerate(reader, start=1):
            total_rows_scanned += len(chunk)

            chunk = chunk[chunk["Stock_symbol"].isin(symbols)]
            if not chunk.empty:
                published = pd.to_datetime(
                    chunk["Date"], utc=True, errors="coerce"
                )
                chunk = chunk.assign(published_at_col=published)
                chunk = chunk[
                    chunk["published_at_col"].notna()
                    & (chunk["published_at_col"] >= start_ts)
                    & (chunk["published_at_col"] < end_ts)
                    & chunk["Article_title"].notna()
                ]

            total_rows_matched += len(chunk)

            if not chunk.empty:
                records = [
                    (
                        row.Stock_symbol,
                        row.published_at_col.to_pydatetime(),
                        row.Article_title,
                        args.source_label,
                    )
                    for row in chunk.itertuples(index=False)
                ]
                with conn.cursor() as cur:
                    for i in range(0, len(records), INSERT_BATCH):
                        cur.executemany(insert_sql, records[i : i + INSERT_BATCH])
                conn.commit()
                total_rows_inserted += len(records)

            elapsed = time.time() - t0
            print(
                f"chunk {chunk_num}: scanned {total_rows_scanned:,} rows, "
                f"matched {total_rows_matched:,}, inserted so far "
                f"{total_rows_inserted:,} ({elapsed:.0f}s elapsed)",
                flush=True,
            )
    finally:
        conn.close()

    print(
        f"\nDone. Scanned {total_rows_scanned:,} rows, "
        f"inserted (or skipped as duplicates) {total_rows_inserted:,} "
        f"rows into historical_headlines."
    )


if __name__ == "__main__":
    main()
