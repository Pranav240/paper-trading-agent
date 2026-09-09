"""
Fetch daily bars for the return-prediction dataset (Phase 04 v2).

WHY THIS EXISTS SEPARATELY FROM THE TRADING SYSTEM
--------------------------------------------------
The agent's `AlpacaPriceSource` fetches a rolling window per symbol per
simulated day, which is right for a decision loop and wrong for building a
training set: it would make thousands of API calls to assemble data that is
one bulk download. This pulls the whole window once, for every symbol, and
writes a flat CSV.

It also deliberately does NOT write to Postgres. The trading system's
database holds operational state; a machine-learning dataset is a
derived artifact rebuilt from source whenever the recipe changes. Keeping
it on disk means the dataset pipeline has no dependency on a running
container — which mattered on the day this was written, when Docker would
not start.

WHAT IT FETCHES
---------------
Every symbol in the list, plus SPY. SPY is not optional: the label in
`build_return_dataset.py` is an EXCESS return, market move subtracted.
Without it a model trained on raw returns learns "the market went up that
week", which is true, useless, and would look like signal.

The window must extend past the last headline date by at least the forward
horizon, or the most recent rows have no label. `--pad-days` handles that.

Run:
    $env:PYTHONPATH="."; python scripts/fetch_bars.py \
        --symbols data/fnspid_symbols.txt --start 2022-06-01 --end 2024-01-20
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from dotenv import load_dotenv

load_dotenv()

MARKET_PROXY = "SPY"
OUT_PATH = Path("data/bars.csv")

# Alpaca accepts a list of symbols per request. Batching cuts 109 requests
# to 6 and stays well inside any response-size limit.
BATCH = 20


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbols",
        default="data/fnspid_symbols.txt",
        help="File with one ticker per line, as written by fnspid_symbol_census.py.",
    )
    parser.add_argument("--start", type=date.fromisoformat, default=date(2022, 6, 1))
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=date(2024, 1, 20),
        help=(
            "Last bar to fetch. Must be at least --pad-days past the last "
            "headline date or the newest rows cannot be labelled."
        ),
    )
    parser.add_argument(
        "--pad-days",
        type=int,
        default=20,
        help="Calendar days of padding the label horizon needs. Checked, not applied.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Only the first N symbols.")
    parser.add_argument("--out", default=str(OUT_PATH))
    return parser.parse_args()


def _load_symbols(path: str, limit: int) -> list[str]:
    raw = Path(path).read_text(encoding="utf-8").split()
    symbols = [s.strip().upper() for s in raw if s.strip()]
    if limit:
        symbols = symbols[:limit]

    # Filtered rather than trusted. The census ranks by headline coverage
    # and happily returns ETFs (SPY, QQQ) and crypto tickers (ETH) —
    # correct for "what has news", wrong for "what is a stock whose
    # returns we are modelling". Anything not resolvable as an equity gets
    # dropped by Alpaca anyway; this just makes the intent explicit.
    drop = {"ETH", "BTC", "QQQ", "SPY", "IWM", "DIA", "VOO"}
    symbols = [s for s in symbols if s not in drop]

    if MARKET_PROXY not in symbols:
        symbols.append(MARKET_PROXY)
    return symbols


def main() -> None:
    args = _parse_args()

    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise SystemExit("ALPACA_API_KEY / ALPACA_SECRET_KEY not set.")

    symbols = _load_symbols(args.symbols, args.limit)
    print(f"{len(symbols)} symbols (including the {MARKET_PROXY} market proxy)")
    print(f"window {args.start} .. {args.end}")

    client = StockHistoricalDataClient(api_key, secret_key)
    rows: list[dict] = []

    for i in range(0, len(symbols), BATCH):
        chunk = symbols[i : i + BATCH]
        request = StockBarsRequest(
            symbol_or_symbols=chunk,
            timeframe=TimeFrame.Day,
            start=datetime.combine(args.start, datetime.min.time()),
            end=datetime.combine(args.end, datetime.min.time()),
            # IEX, not SIP — the same free-tier constraint AlpacaPriceSource
            # documents. Daily closes from a single exchange are fine for a
            # 5-day return label; they would not be for intraday work.
            feed=DataFeed.IEX,
        )
        bar_set = client.get_stock_bars(request)
        got = 0
        for symbol in chunk:
            for bar in bar_set.data.get(symbol, []):
                rows.append(
                    {
                        "symbol": symbol,
                        "day": bar.timestamp.date().isoformat(),
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                    }
                )
                got += 1
        print(f"  batch {i // BATCH + 1}: {len(chunk)} symbols, {got} bars")

    if not rows:
        raise SystemExit("No bars returned — check the keys and the date range.")

    rows.sort(key=lambda r: (r["symbol"], r["day"]))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    covered = sorted({r["symbol"] for r in rows})
    missing = sorted(set(symbols) - set(covered))
    days = sorted({r["day"] for r in rows})

    print(f"\nwrote {out} — {len(rows):,} bars, {len(covered)} symbols, {len(days)} trading days")
    print(f"first {days[0]}  last {days[-1]}")
    if missing:
        # Named rather than silently dropped: a symbol the census promised
        # and Alpaca does not have is a coverage gap the dataset builder
        # needs to know about, not a rounding error.
        print(f"NO BARS for {len(missing)}: {' '.join(missing)}")
    if MARKET_PROXY not in covered:
        raise SystemExit(
            f"\n{MARKET_PROXY} returned no bars. The excess-return label cannot be "
            "computed without it — fix this before building the dataset."
        )

    last = date.fromisoformat(days[-1])
    headline_end = args.end - timedelta(days=args.pad_days)
    print(
        f"\nLabelling headlines up to {headline_end} needs bars past that date; "
        f"last bar is {last} ({(last - headline_end).days} days of padding)."
    )


if __name__ == "__main__":
    main()
