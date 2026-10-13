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
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

DEFAULT_DATA = Path(__file__).resolve().parent / "data"
REPO_ROOT = Path(__file__).resolve().parent.parent
# Every row in fact_eval_metrics.csv must cite one of these repo files or folders.
# Add a path here only when a metric genuinely comes from it.
ALLOWED_SOURCES = ("PROJECT.md", "README.md", "docs/engineering-log.md", "tests/")
LEAK_FREE = "Both leaks fixed"  # same literal as the Leak-Free Runs measure


def load(data_dir: Path) -> dict[str, pd.DataFrame]:
    names = ["fact_daily_decisions", "fact_trades", "fact_backtest_runs", "fact_eval_metrics"]
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


BASES = {"recorded_run", "v1_rules_replay"}
# What each result_basis value means in the report.
REPLAY_NAME = "V1 rules replayed on the same decisions"


def recorded_name(basis: str) -> str:
    """How a run's own recorded trades should be labelled. Runs #3-#14 were recorded under
    V1's rules; the three V2 reruns were recorded with the VaR node."""
    return "recorded (VaR node)" if basis == "v1_rules_replay" else "recorded (V1 rules)"


def run_list(ids) -> str:
    return ", ".join(f"#{i}" for i in ids)


def recorded_basis_label(b: pd.DataFrame) -> str:
    """Basis of anything computed from fact_daily_decisions / fact_trades."""
    v1 = b.loc[b["result_basis"] != "v1_rules_replay", "backtest_id"]
    var = b.loc[b["result_basis"] == "v1_rules_replay", "backtest_id"]
    return f"recorded (V1 rules: {len(v1)} runs; VaR node: {run_list(var)})"


def result_basis_label(sel: pd.DataFrame) -> str:
    """Basis of anything computed from fact_backtest_runs.result_usd for the selected runs."""
    parts = []
    for basis, g in sel.groupby("result_basis"):
        name = REPLAY_NAME if basis == "v1_rules_replay" else "recorded (V1 rules)"
        parts.append(f"{name}: {run_list(g['backtest_id'])}")
    return "; ".join(parts)


def recorded_mtm(t: pd.DataFrame, d: pd.DataFrame, run: int) -> tuple[float, int, int]:
    """Mark-to-market return of a run's own recorded trades, from the CSVs alone.

    closed lots' realized_pnl + open lots marked at the run's last decision-day price.
    Returns (return_usd, closed_lots, open_lots).
    """
    tt = t[t["backtest_id"] == run]
    last_price = d[d["backtest_id"] == run].sort_values("as_of").iloc[-1]["price"]
    closed, opened = tt[tt["status"] == "CLOSED"], tt[tt["status"] == "OPEN"]
    unrealized = (opened["quantity"] * (last_price - opened["entry_price"])).sum()
    return float(closed["realized_pnl"].sum() + unrealized), len(closed), len(opened)


def recorded_derivation_check(b: pd.DataFrame, t: pd.DataFrame, d: pd.DataFrame) -> bool:
    """For runs whose result_basis is recorded_run, the return derived from fact_trades and
    fact_daily_decisions must equal result_usd. This validates the derivation that the
    side-by-side table below uses for the VaR-node runs."""
    ok = True
    print("\nRecorded-return derivation vs published result_usd (recorded_run basis)")
    for r in b[b["result_basis"] == "recorded_run"].sort_values("backtest_id").itertuples():
        mtm, _, _ = recorded_mtm(t, d, r.backtest_id)
        good = abs(mtm - r.result_usd) <= 0.011
        ok = ok and good
        print(f"  #{r.backtest_id:<4} derived={mtm:>9.2f} result_usd={r.result_usd:>9.2f}  {'ok' if good else 'FAIL'}")
    return ok


def side_by_side(b: pd.DataFrame, t: pd.DataFrame, d: pd.DataFrame) -> None:
    """Both bases for the runs that have two: trade count and return."""
    print("\nBoth bases, runs recorded with the VaR node")
    print(f"  {'run':<5} {'basis':<42} {'closed':>6} {'open':>5} {'return $':>9} {'vs B&H $':>9}")
    for r in b[b["result_basis"] == "v1_rules_replay"].sort_values("backtest_id").itertuples():
        mtm, closed, opened = recorded_mtm(t, d, r.backtest_id)
        print(f"  #{r.backtest_id:<4} {recorded_name(r.result_basis):<42} {closed:>6} {opened:>5} {mtm:>9.2f} {mtm - r.buy_hold_usd:>+9.2f}")
        print(f"  #{r.backtest_id:<4} {REPLAY_NAME:<42} {r.closed_trades:>6} {'-':>5} {r.result_usd:>9.2f} {r.result_usd - r.buy_hold_usd:>+9.2f}")


def trade_count_check(b: pd.DataFrame, t: pd.DataFrame) -> bool:
    """fact_backtest_runs.closed_trades must equal the CLOSED rows in fact_trades.

    The only accepted difference is a run whose result_basis is "v1_rules_replay":
    its closed_trades counts the trades of V1's rules replayed over the run's
    decisions, while fact_trades holds the trades the run itself recorded (with
    the VaR node). Any other difference, an unknown basis, or a run missing from
    either file is a failure.
    """
    ok = True
    if "result_basis" not in b.columns:
        print("\nFAIL: fact_backtest_runs.csv has no result_basis column")
        return False
    counts = t[t["status"] == "CLOSED"].groupby("backtest_id").size()
    print("\nTrade counts: fact_backtest_runs.closed_trades vs CLOSED rows in fact_trades")
    for r in b.sort_values("backtest_id").itertuples():
        n = int(counts.get(r.backtest_id, 0))
        if r.result_basis not in BASES:
            verdict, ok = f"FAIL (unknown result_basis {r.result_basis!r})", False
        elif n == r.closed_trades:
            verdict = "ok"
        elif r.result_basis == "v1_rules_replay":
            verdict = "differs, declared (v1_rules_replay)"
        else:
            verdict, ok = "FAIL (unexplained difference)", False
        print(f"  #{r.backtest_id:<4} runs={r.closed_trades:<3} trades={n:<3} {verdict}")
    unknown = sorted(set(t["backtest_id"]) - set(b["backtest_id"]))
    if unknown:
        print(f"  FAIL: fact_trades has backtest_id(s) missing from fact_backtest_runs: {unknown}")
        ok = False
    return ok


def source_label_check(e: pd.DataFrame) -> bool:
    """Every fact_eval_metrics.source must be in ALLOWED_SOURCES and exist in this repo."""
    ok = True
    print("\nEvaluation-metric source labels")
    for src, n in e["source"].fillna("(blank)").value_counts().items():
        if src not in ALLOWED_SOURCES:
            print(f"  FAIL: {n} row(s) cite {src!r}, which is not in the allowed list")
            ok = False
        elif not (REPO_ROOT / src).exists():
            print(f"  FAIL: {n} row(s) cite {src!r}, which does not exist in the repo")
            ok = False
        else:
            print(f"  ok: {n} row(s) cite {src}")
    return ok


def freshness(data_dir: Path) -> None:
    """Print when the CSVs were exported and warn (never fail) if the ref has moved on."""
    info_path = data_dir / "export_info.json"
    if not info_path.exists():
        print("\nExport info: export_info.json not found (regenerate with powerbi/build_data.py)")
        return
    info = json.loads(info_path.read_text(encoding="utf-8"))
    print(f"\nData exported {info['exported_on']} from {info['git_ref']} "
          f"at {info['latest_commit']} ({info['latest_commit_date'][:10]})")
    try:
        now = subprocess.run(["git", "rev-parse", info["git_ref"]], cwd=REPO_ROOT, check=True,
                             capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return  # not inside a git checkout; nothing to compare with
    if now != info["git_ref_sha"]:
        print(f"WARNING: {info['git_ref']} is now at {now[:7]}; the commit data is behind. "
              f"Refresh with: python powerbi/build_data.py")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA, help="folder holding the CSVs")
    args = ap.parse_args()

    f = load(args.data)
    d, t, b, e = f["fact_daily_decisions"], f["fact_trades"], f["fact_backtest_runs"], f["fact_eval_metrics"]

    lf = b[b["look_ahead_status"] == LEAK_FREE]
    rec = recorded_basis_label(b) if "result_basis" in b.columns else "recorded"
    rows = [
        ("Decisions", f"{decisions(d):,}", rec),
        ("HOLD %", f"{hold_pct(d):.2%}", rec),
        ("Closed Trades", f"{closed_trades(t):,}", rec),
        ("Win Rate", f"{win_rate(t):.2%}", rec),
        ("Leak-Free Diff $", f"{leak_free_diff(b):+.2f}",
         result_basis_label(lf) if "result_basis" in b.columns else "unknown"),
    ]
    width = max(len(n) for n, _, _ in rows)
    vwidth = max(len(v) for _, v, _ in rows)
    print(f"Data folder: {args.data}")
    for name, value, basis in rows:
        print(f"{name:<{width}}  {value:<{vwidth}}  basis: {basis}")

    ok = trade_count_check(b, t)
    ok = recorded_derivation_check(b, t, d) and ok
    side_by_side(b, t, d)
    ok = source_label_check(e) and ok
    freshness(args.data)

    # Sanity check on the source column the DAX does not use: diff_usd must
    # equal result_usd - buy_hold_usd on every run.
    bad = b[(b["result_usd"] - b["buy_hold_usd"] - b["diff_usd"]).abs() > 0.011]
    if len(bad):
        print(f"\nWARNING: diff_usd != result_usd - buy_hold_usd for backtest_id(s): "
              f"{', '.join(map(str, bad['backtest_id']))}")
        ok = False
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
