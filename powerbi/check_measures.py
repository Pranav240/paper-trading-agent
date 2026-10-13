"""Recompute headline Power BI measures from the CSVs in powerbi/data/ with pandas.

Purpose: an independent check. The numbers printed here should equal the
matching cards in the Power BI report (see powerbi/README.md). Each function
mirrors the DAX in measures.dax; the DAX definition is quoted next to each.

Run from anywhere:
    python powerbi/check_measures.py
    python powerbi/check_measures.py --data path/to/other/data

Not recomputed here: the VaR breach rate. It needs the per-decision VaR
forecasts (the `var_forecasts` table in Postgres), which are not in
powerbi/data/. The report shows the stored value from fact_eval_metrics.csv;
the code that produces it is scripts/evaluate_var.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

DEFAULT_DATA = Path(__file__).resolve().parent / "data"
LEAK_FREE = "Both leaks fixed"  # same literal as the Leak-Free Runs measure


def load(data_dir: Path) -> dict[str, pd.DataFrame]:
    names = ["fact_daily_decisions", "fact_trades", "fact_backtest_runs"]
    missing = [n for n in names if not (data_dir / f"{n}.csv").exists()]
    if missing:
        sys.exit(f"Missing CSV(s) in {data_dir}: {', '.join(m + '.csv' for m in missing)}")
    return {n: pd.read_csv(data_dir / f"{n}.csv") for n in names}


def decisions(d: pd.DataFrame) -> int:
    # Decisions = COUNTROWS ( fact_daily_decisions )
    return len(d)


def hold_pct(d: pd.DataFrame) -> float:
    # HOLD % = DIVIDE ( CALCULATE ( [Decisions], action = "HOLD" ), [Decisions] )
    return (d["action"] == "HOLD").sum() / len(d)


def closed_trades(t: pd.DataFrame) -> int:
    # Closed Trades = CALCULATE ( COUNTROWS ( fact_trades ), status = "CLOSED" )
    return int((t["status"] == "CLOSED").sum())


def win_rate(t: pd.DataFrame) -> float:
    # Win Rate = DIVIDE ( CALCULATE ( COUNTROWS ( fact_trades ),
    #                                 status = "CLOSED", realized_pnl > 0 ), [Closed Trades] )
    closed = t[t["status"] == "CLOSED"]
    return (closed["realized_pnl"] > 0).sum() / len(closed)


def leak_free_diff(b: pd.DataFrame) -> float:
    # Leak-Free Diff $ = CALCULATE ( [Diff vs B&H $], look_ahead_status = "Both leaks fixed" )
    #   where Diff vs B&H $ = SUM ( result_usd ) - SUM ( buy_hold_usd )
    sel = b[b["look_ahead_status"] == LEAK_FREE]
    return sel["result_usd"].sum() - sel["buy_hold_usd"].sum()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA, help="folder holding the CSVs")
    args = ap.parse_args()

    f = load(args.data)
    d, t, b = f["fact_daily_decisions"], f["fact_trades"], f["fact_backtest_runs"]

    rows = [
        ("Decisions", f"{decisions(d):,}"),
        ("HOLD %", f"{hold_pct(d):.2%}"),
        ("Closed Trades", f"{closed_trades(t):,}"),
        ("Win Rate", f"{win_rate(t):.2%}"),
        ("Leak-Free Diff $", f"{leak_free_diff(b):+.2f}"),
    ]
    width = max(len(n) for n, _ in rows)
    print(f"Data folder: {args.data}")
    for name, value in rows:
        print(f"{name:<{width}}  {value}")

    # Sanity check on the source column the DAX does not use: diff_usd must
    # equal result_usd - buy_hold_usd on every run.
    bad = b[(b["result_usd"] - b["buy_hold_usd"] - b["diff_usd"]).abs() > 0.011]
    if len(bad):
        print(f"\nWARNING: diff_usd != result_usd - buy_hold_usd for backtest_id(s): "
              f"{', '.join(map(str, bad['backtest_id']))}")
        sys.exit(1)


if __name__ == "__main__":
    main()
