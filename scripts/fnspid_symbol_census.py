"""
Which FNSPID symbols actually have dense enough headlines to backtest?

The open question after the V1 verdict is whether "no edge" is a fact
about the strategy or a fact about AAPL — one symbol is not a result.
Answering it needs more symbols, and the blocker is headline supply.
`data/nasdaq_exteral_data.csv` (23GB, already on disk) is the cheapest
source, but only for symbols it covers *continuously* over a usable
window.

That "continuously" is the whole point. Design decision 8 exists because
year-level totals hid a five-month gap that made `backtest_id=2`
uninformative: a symbol with 4,000 headlines in 2022 is useless if 3,900
of them are in one month. So this counts per symbol per MONTH and ranks
by covered months, not by volume.

One pass over the whole file for every symbol at once — the existing
`scripts/diagnose_fnspid_dates.py` re-reads all 23GB per symbol, which is
fine for checking one and hopeless for choosing among thousands. Only
`Date` and `Stock_symbol` are read (`usecols`), which is what keeps a
23GB file tractable at all.

Run (takes a while — it streams the entire file):

    python scripts/fnspid_symbol_census.py
    python scripts/fnspid_symbol_census.py --start 2022-06 --end 2023-06 --min-per-month 15

Output: symbols ranked by how many months in the window clear
`--min-per-month`, with the thinnest month shown, because the thinnest
month is what actually limits a backtest.
"""

from __future__ import annotations

import argparse
from collections import defaultdict

import pandas as pd

CSV_PATH = "data/nasdaq_exteral_data.csv"
CHUNK_ROWS = 200_000
USE_COLS = ["Date", "Stock_symbol"]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start", default="2022-06", help="First month to count, YYYY-MM."
    )
    parser.add_argument(
        "--end", default="2023-12", help="Last month to count, inclusive, YYYY-MM."
    )
    parser.add_argument(
        "--min-per-month",
        type=int,
        default=15,
        help=(
            "A month counts as covered at or above this many headlines. "
            "Default 15 — roughly one headline every other trading day, "
            "below which the sentiment node short-circuits on most days."
        ),
    )
    parser.add_argument(
        "--top", type=int, default=40, help="How many symbols to print."
    )
    parser.add_argument("--csv", default=CSV_PATH)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    start = pd.Period(args.start, freq="M")
    end = pd.Period(args.end, freq="M")
    n_months = (end - start).n + 1
    wanted = {str(start + i) for i in range(n_months)}

    # symbol -> {month string -> count}. Bounded by the window, so this
    # stays small however big the file gets.
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    rows_seen = 0

    reader = pd.read_csv(
        args.csv,
        usecols=USE_COLS,
        dtype=str,
        chunksize=CHUNK_ROWS,
        on_bad_lines="warn",
        encoding="utf-8",
    )

    for chunk in reader:
        rows_seen += len(chunk)
        chunk = chunk.dropna(subset=["Date", "Stock_symbol"])
        if chunk.empty:
            continue

        # errors="coerce" for the same reason import_fnspid.py uses it:
        # the file has malformed dates. diagnose_fnspid_dates.py confirmed
        # for AAPL that coercion drops nothing real, but that was checked,
        # not assumed — re-check any symbol before trusting its results.
        parsed = pd.to_datetime(chunk["Date"], utc=True, errors="coerce")
        chunk = chunk[parsed.notna()]
        if chunk.empty:
            continue

        months = parsed[parsed.notna()].dt.strftime("%Y-%m")
        for symbol, month in zip(chunk["Stock_symbol"].values, months.values):
            if month in wanted:
                counts[symbol][month] += 1

        if rows_seen % (CHUNK_ROWS * 25) == 0:
            print(f"  ... {rows_seen:,} rows scanned, {len(counts)} symbols seen")

    print(f"\nscanned {rows_seen:,} rows; {len(counts)} symbols appear in "
          f"{args.start}..{args.end}\n")

    ranked = []
    for symbol, months in counts.items():
        covered = sum(1 for m in wanted if months.get(m, 0) >= args.min_per_month)
        thinnest = min(months.get(m, 0) for m in wanted)
        ranked.append((covered, sum(months.values()), thinnest, symbol))
    ranked.sort(reverse=True)

    print(f"=== symbols by months covered (>= {args.min_per_month}/month, "
          f"{n_months} months in window) ===")
    print(f"  {'symbol':<8} {'months':>7} {'total':>8} {'thinnest month':>15}")
    print("  " + "-" * 42)
    for covered, total, thinnest, symbol in ranked[: args.top]:
        print(f"  {symbol:<8} {covered:>7} {total:>8} {thinnest:>15}")

    full = [r for r in ranked if r[0] == n_months]
    print(
        f"\n{len(full)} symbol(s) cover ALL {n_months} months at "
        f">= {args.min_per_month}/month — those are the ones worth "
        "backtesting without a coverage caveat."
    )
    if full:
        print("  " + " ".join(r[3] for r in full[:30]))


if __name__ == "__main__":
    main()
