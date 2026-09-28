"""
VaR risk node vs the V1 rule-based node, on the same decisions
(V2, phase 07, step 6b). Zero API calls: works on a finished backtest.

WHY A REPLAY AND NOT A SECOND LLM RUN
-------------------------------------
The Portfolio Manager sees only the two analysts' opinions, never the
position, so its proposals don't depend on what the risk node did. Every
proposal of a VaR-node backtest is therefore exactly what a rule-based
run over the same days would have been handed. Replaying the V1 rules
over that tape isolates the risk node's effect; a second LLM run would
mix it with run-to-run LLM noise ($24-90 between V1's repeat runs).

The replay is only trusted if it first reproduces the recorded VaR-node
run exactly (same gate as replay_risk_rules.py).

PRE-REGISTERED (committed before the backtest it reads had finished)
--------------------------------------------------------------------
- Primary: mark-to-market P&L, VaR node minus V1 rules, and each against
  buy-and-hold of the 20-share cap (the trivial baseline).
- Secondary: max drawdown of the daily mark-to-market P&L curve, and
  average shares held.
- One symbol, one window, one path: no significance claim is possible,
  and none is made. A difference is reported as a difference.
- Expectation, stated in advance: the 2% budget binds whenever VaR > 2%,
  and run #4's window averaged 3.5% (evaluate_var.py). The node will hold
  less in a market that rose ~33%, so lower P&L is the likely outcome; a
  smaller drawdown is the only thing it could plausibly buy.

Run:
    $env:PYTHONPATH="."; .\\papertrading\\Scripts\\python.exe scripts/compare_var_node.py <backtest_id>
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()

from app.agent.backtest import DEFAULT_SLIPPAGE_BPS
from app.agent.risk_manager import MAX_POSITION_QTY
from replay_risk_rules import RECORDED, replay

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)

TAPE = """
SELECT
    r.as_of,
    pm.opinion                        AS proposed_action,
    (pm.raw_output->>'quantity')::int AS proposed_qty,
    -- Fill price: the day's open where recorded (migration 007), else
    -- V1's current_price, which old runs filled at.
    COALESCE(d.execution_price,
             (d.technicals_snapshot->>'current_price')::numeric) AS price,
    vf.var_max_qty,
    vf.var
FROM decisions d
JOIN runs r ON d.run_id = r.id
JOIN agent_opinions pm
    ON pm.decision_id = d.id AND pm.agent_name = 'portfolio_manager'
LEFT JOIN var_forecasts vf ON vf.decision_id = d.id
WHERE r.backtest_id = %s
  AND d.symbol = %s
  AND d.technicals_snapshot->>'current_price' IS NOT NULL
ORDER BY r.as_of
"""


def path_stats(tape: list[dict], **rule) -> tuple[Decimal, Decimal, float]:
    """(final MtM, max drawdown of the daily MtM curve, avg shares held)."""
    curve, held = [], []
    for k in range(len(tape)):
        res = replay(tape[: k + 1], name="", slippage_bps=DEFAULT_SLIPPAGE_BPS, **rule)
        curve.append(res.mark_to_market(tape[k]["price"]))
        held.append(sum(lot.quantity for lot in res.open_lots))
    peak, max_dd = curve[0], Decimal("0")
    for value in curve:
        peak = max(peak, value)
        max_dd = max(max_dd, peak - value)
    return curve[-1], max_dd, sum(held) / len(held)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python scripts/compare_var_node.py <backtest_id>")
    backtest_id = int(sys.argv[1])
    symbol = sys.argv[2] if len(sys.argv) > 2 else "AAPL"

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(TAPE, (backtest_id, symbol))
            tape = cur.fetchall()
            cur.execute(RECORDED, (backtest_id, symbol))
            recorded = cur.fetchone()
    if not tape:
        raise SystemExit(f"No decision tape for backtest {backtest_id}.")

    first, last = tape[0]["price"], tape[-1]["price"]
    with_var = sum(1 for r in tape if r["var_max_qty"] is not None)
    binding = sum(1 for r in tape if r["var_max_qty"] is not None and r["var_max_qty"] < MAX_POSITION_QTY)
    print(f"backtest {backtest_id}  {symbol}  {tape[0]['as_of'].date()} .. {tape[-1]['as_of'].date()}  "
          f"({len(tape)} decision days, VaR on {with_var}, budget below {MAX_POSITION_QTY} shares on {binding})")
    print(f"price {first:.2f} -> {last:.2f}\n")

    # --- correctness gate: replaying the VaR node must reproduce the run.
    var_run = replay(tape, name="VaR node", slippage_bps=DEFAULT_SLIPPAGE_BPS, var_budget=True)
    recorded_mtm = recorded["realized"] + (
        last * recorded["open_qty"] - recorded["open_cost"] if recorded["open_qty"] else Decimal("0")
    )
    drift = abs(var_run.mark_to_market(last) - recorded_mtm)
    print(f"replay check: recorded MtM {recorded_mtm:+.2f}, replayed {var_run.mark_to_market(last):+.2f}, "
          f"drift {drift:.4f}")
    if drift > Decimal("0.05"):
        raise SystemExit("MISMATCH: replay does not reproduce the recorded run. Stopping.")
    print()

    buy_hold = (last - first) * MAX_POSITION_QTY
    capital = first * MAX_POSITION_QTY
    rows = [
        ("VaR node (recorded)", {"var_budget": True}),
        ("V1 rules (replayed)", {}),
    ]
    print(f"{'':<22} {'MtM P&L':>10} {'%cap':>7} {'vs B&H':>9} {'max DD':>9} {'avg sh':>7} {'buys':>5} {'blocked':>8}")
    results = {}
    for name, rule in rows:
        mtm, dd, avg_held = path_stats(tape, **rule)
        res = replay(tape, name=name, slippage_bps=DEFAULT_SLIPPAGE_BPS, **rule)
        results[name] = (mtm, dd)
        print(f"{name:<22} {mtm:>+10.2f} {mtm / capital:>7.2%} {mtm - buy_hold:>+9.2f} "
              f"{dd:>9.2f} {avg_held:>7.1f} {res.buys:>5} {res.blocked:>8}")
    print(f"{'buy & hold 20 sh':<22} {buy_hold:>+10.2f} {buy_hold / capital:>7.2%}")

    (v_mtm, v_dd), (b_mtm, b_dd) = results["VaR node (recorded)"], results["V1 rules (replayed)"]
    print(f"\nVaR node minus V1: P&L {v_mtm - b_mtm:+.2f}, max drawdown {v_dd - b_dd:+.2f}")
    print("One symbol, one window, one path: a difference, not a significance result.")


if __name__ == "__main__":
    main()
