"""
Build the return-prediction training set (Phase 04 v2).

WHY THE TARGET CHANGED
----------------------
Phase 04 v1 trained a local model to imitate GPT-4o-mini's BUY/HOLD/SELL
opinion. That failed twice, for one reason: the teacher said HOLD on 96.8%
of calls, so the loss minimum was the constant function and both adapters
found it. Worse, an ablation showed the teacher's opinion was making the
trading system *worse*, so even a perfect imitation would have been of
something worth deleting.

This trains on what actually happened instead. Each example is a block of
headlines, and the label is the stock's subsequent excess return. The
labels come from price data, so they are free, continuous, and have real
variance — none of the three things that killed v1.

THREE LABEL DECISIONS, EACH LOAD-BEARING
----------------------------------------
1. EXCESS, not raw. The market's move is subtracted using SPY. A model
   trained on raw returns learns "that week the market went up", which is
   true, useless, and looks exactly like signal on a validation set drawn
   from the same period.
2. Z-SCORED PER SYMBOL. TSLA's daily range dwarfs KO's. Without this the
   loss is dominated by whichever tickers happen to be volatile, and the
   model spends its capacity learning which symbol it is looking at.
3. CLIPPED at +/-3 sigma. One earnings gap otherwise contributes more
   gradient than a hundred ordinary days.

LOOK-AHEAD: headlines are taken strictly at or before the decision day's
close; the return is measured strictly after it. That mirrors
`HistoricalHeadlineSource`, which enforces `published_at < as_of` for the
same reason.

THE SPLIT IS BY TIME, AND SEPARATELY BY SYMBOL. A random split leaks
badly here: consecutive days share most of their headlines through the
3-day lookback window, so near-duplicate rows would land on both sides and
report a validation score that is really a memorisation score.

Run:
    $env:PYTHONPATH="."; python scripts/build_return_dataset.py
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

CSV_PATH = "data/nasdaq_exteral_data.csv"
BARS_PATH = "data/bars.csv"
SYMBOLS_PATH = "data/fnspid_symbols.txt"
OUT_DIR = Path("data/return_dataset")

CHUNK_ROWS = 200_000
USE_COLS = ["Date", "Article_title", "Stock_symbol"]
MARKET_PROXY = "SPY"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=CSV_PATH)
    parser.add_argument("--bars", default=BARS_PATH)
    parser.add_argument("--symbols", default=SYMBOLS_PATH)
    parser.add_argument("--out", default=str(OUT_DIR))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2022, 6, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2023, 12, 31))
    parser.add_argument(
        "--horizon",
        type=int,
        default=5,
        help=(
            "Trading days ahead the label measures. 5 (one week) trades noise "
            "for attribution: 1 day is mostly microstructure, 20 days is barely "
            "about the headline any more."
        ),
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=3,
        help="Headline window, matching the live sentiment node's default.",
    )
    parser.add_argument(
        "--max-headlines",
        type=int,
        default=25,
        help=(
            "Cap per example. Dense days carry 70+ headlines, which blows past "
            "the context window and is mostly repetition of the same story."
        ),
    )
    parser.add_argument(
        "--val-start",
        type=date.fromisoformat,
        default=date(2023, 8, 1),
        help="Time split. Everything from this date is validation.",
    )
    parser.add_argument(
        "--holdout-symbols",
        type=int,
        default=15,
        help=(
            "Symbols held out entirely, to test generalisation to tickers the "
            "model has never seen. Taken from the end of the list so the "
            "densest names stay in training."
        ),
    )
    return parser.parse_args()


# --------------------------------------------------------------------------
# prices
# --------------------------------------------------------------------------

def load_bars(path: str) -> tuple[dict, dict]:
    """Returns (closes, ordinals).

    `closes[symbol][day] -> close` and `ordinals[symbol] -> [day, ...]`
    sorted. The ordinal list is what makes "5 trading days later" mean
    trading days rather than calendar days — the distinction matters over
    weekends and holidays, and getting it wrong silently shortens the
    horizon by ~28%.
    """
    df = pd.read_csv(path)
    closes: dict[str, dict[date, float]] = defaultdict(dict)
    for symbol, day, close in zip(df["symbol"], df["day"], df["close"]):
        closes[symbol][date.fromisoformat(day)] = float(close)
    ordinals = {s: sorted(d.keys()) for s, d in closes.items()}
    return closes, ordinals


def forward_return(
    closes: dict, ordinals: dict, symbol: str, day: date, horizon: int
) -> float | None:
    days = ordinals.get(symbol)
    if not days:
        return None
    # bisect over a sorted list: find the first trading day >= `day`, which
    # handles a headline landing on a weekend or a market holiday.
    lo, hi = 0, len(days)
    while lo < hi:
        mid = (lo + hi) // 2
        if days[mid] < day:
            lo = mid + 1
        else:
            hi = mid
    if lo >= len(days) or lo + horizon >= len(days):
        return None
    d0, d1 = days[lo], days[lo + horizon]
    c0, c1 = closes[symbol][d0], closes[symbol][d1]
    if c0 <= 0:
        return None
    return (c1 - c0) / c0


# --------------------------------------------------------------------------
# headlines
# --------------------------------------------------------------------------

def load_headlines(csv_path: str, symbols: set[str], start: date, end: date) -> dict:
    """symbol -> {day -> [headline, ...]}, streamed out of the 23GB file."""
    per_day: dict[str, dict[date, list[str]]] = defaultdict(lambda: defaultdict(list))
    seen = 0

    reader = pd.read_csv(
        csv_path,
        usecols=USE_COLS,
        dtype=str,
        chunksize=CHUNK_ROWS,
        on_bad_lines="warn",
        encoding="utf-8",
    )
    for chunk in reader:
        seen += len(chunk)
        chunk = chunk[chunk["Stock_symbol"].isin(symbols)]
        chunk = chunk.dropna(subset=["Date", "Article_title"])
        if chunk.empty:
            continue
        parsed = pd.to_datetime(chunk["Date"], utc=True, errors="coerce")
        chunk = chunk[parsed.notna()]
        if chunk.empty:
            continue
        days = parsed[parsed.notna()].dt.date
        for symbol, day, title in zip(
            chunk["Stock_symbol"].values, days.values, chunk["Article_title"].values
        ):
            if start <= day <= end:
                per_day[symbol][day].append(str(title).strip())
        if seen % (CHUNK_ROWS * 25) == 0:
            print(f"  ... {seen:,} rows scanned")

    print(f"scanned {seen:,} rows")
    return per_day


# --------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()

    all_symbols = [
        s.strip().upper()
        for s in Path(args.symbols).read_text(encoding="utf-8").split()
        if s.strip()
    ]
    drop = {"ETH", "BTC", "QQQ", "SPY", "IWM", "DIA", "VOO"}
    all_symbols = [s for s in all_symbols if s not in drop]

    holdout = set(all_symbols[-args.holdout_symbols :]) if args.holdout_symbols else set()
    print(f"{len(all_symbols)} symbols, {len(holdout)} held out entirely")

    print("\nloading bars...")
    closes, ordinals = load_bars(args.bars)
    if MARKET_PROXY not in closes:
        raise SystemExit(f"{MARKET_PROXY} missing from {args.bars} — rerun fetch_bars.py")
    print(f"  {len(closes)} symbols, {sum(len(v) for v in closes.values()):,} bars")

    print("\nloading headlines (this streams the whole CSV)...")
    per_day = load_headlines(args.csv, set(all_symbols), args.start, args.end)
    print(f"  {len(per_day)} symbols have headlines in range")

    # ---- assemble ---------------------------------------------------------
    raw: list[dict] = []
    for symbol, by_day in per_day.items():
        for day in sorted(by_day):
            # The 3-day lookback the live node uses, rebuilt here so the
            # model trains on exactly the shape it will see at inference.
            window = [
                h
                for d in range(args.lookback_days)
                for h in by_day.get(day - timedelta(days=d), [])
            ]
            if not window:
                continue

            fwd = forward_return(closes, ordinals, symbol, day, args.horizon)
            mkt = forward_return(closes, ordinals, MARKET_PROXY, day, args.horizon)
            if fwd is None or mkt is None:
                continue

            raw.append(
                {
                    "symbol": symbol,
                    "day": day.isoformat(),
                    "headlines": window[: args.max_headlines],
                    "n_headlines": len(window),
                    "excess_return": fwd - mkt,
                }
            )

    if not raw:
        raise SystemExit("No examples built — check the date range and bars coverage.")
    print(f"\n{len(raw):,} raw examples")

    # ---- normalise per symbol --------------------------------------------
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for r in raw:
        by_symbol[r["symbol"]].append(r)

    examples = []
    for symbol, rows in by_symbol.items():
        vals = [r["excess_return"] for r in rows]
        if len(vals) < 30:
            # Too few points to estimate a mean and standard deviation from.
            # Normalising on 5 observations produces a scale that is mostly
            # noise, and every row from that symbol inherits it.
            continue
        mu = statistics.fmean(vals)
        sd = statistics.stdev(vals)
        if sd <= 0:
            continue
        for r in rows:
            z = (r["excess_return"] - mu) / sd
            r["label"] = max(-3.0, min(3.0, z))
            examples.append(r)

    print(f"{len(examples):,} labelled examples across {len(by_symbol)} symbols")

    # ---- split ------------------------------------------------------------
    splits: dict[str, list[dict]] = {"train": [], "val_time": [], "val_symbol": []}
    for r in examples:
        if r["symbol"] in holdout:
            splits["val_symbol"].append(r)
        elif date.fromisoformat(r["day"]) >= args.val_start:
            splits["val_time"].append(r)
        else:
            splits["train"].append(r)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in splits.items():
        rows.sort(key=lambda r: (r["day"], r["symbol"]))
        path = out / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        if rows:
            labels = [r["label"] for r in rows]
            print(
                f"\n{name:<11} {len(rows):>6,} rows  "
                f"mean {statistics.fmean(labels):+.3f}  "
                f"sd {statistics.stdev(labels):.3f}  "
                f"{path}"
            )
        else:
            print(f"\n{name:<11} {0:>6}  (empty)")

    print(
        "\nLabel is a per-symbol z-scored 5-day excess return, clipped at +/-3.\n"
        "A trivial model predicting 0 everywhere therefore scores MAE ~0.8 and\n"
        "R^2 = 0. Beat that before believing anything — see design decision 11."
    )


if __name__ == "__main__":
    main()
