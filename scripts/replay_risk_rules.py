"""
Replay a finished backtest through alternative Risk Manager rules, offline.

WHY THIS COSTS NOTHING
----------------------
The Risk Manager is rule-based — no LLM. Everything upstream of it is
already recorded: the Portfolio Manager's proposed action and quantity
live in `agent_opinions`, and the price the agent saw that day lives in
`decisions.technicals_snapshot`. So "what would have happened under a
different risk rule?" is answerable by re-running the arithmetic over
recorded data. Zero API calls, zero new backtests.

WHAT QUESTION IT ANSWERS
------------------------
From the V1 verdict: backtest 6 (out-of-sample, Jul-Dec 2023) lost $63
marked-to-market in a market that was essentially flat. The visible
pattern was three straight BUYs on 2023-12-19/20/21 at 197.02 -> 195.04
-> 194.80 — buying into a decline with nothing in the system saying
"stop adding", because the only risk rule is a flat 20-share cap. Would a
drawdown-based rule have avoided most of that?

WHAT IT CANNOT ANSWER — READ THIS BEFORE QUOTING ANY NUMBER
-----------------------------------------------------------
This is rule-fitting on the single window that motivated the rule, on one
symbol. Every number it prints is in-sample by construction. A rule that
looks good here has NOT been validated; it has been selected. Treat the
output as a hypothesis worth testing out-of-sample later, never as
evidence that the rule works. That is the same discipline that made
backtest 6 itself trustworthy (design decision 10) and it applies with
more force here, not less.

The baseline row is the real check on this script: it replays the
EXISTING rules and must reproduce the recorded result exactly. If it
doesn't, the replay is wrong and none of the other rows mean anything —
the script says so and stops.

Run:
    $env:PYTHONPATH="."; python scripts/replay_risk_rules.py 6
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()

from app.agent.backtest import COMMISSION, DEFAULT_SLIPPAGE_BPS
from app.agent.risk_manager import MAX_POSITION_QTY

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)

# The Portfolio Manager's PROPOSAL, not the final action — the whole point
# is to re-gate the proposal through a different risk rule. Its quantity
# lives in raw_output because TentativeDecision.quantity isn't a column.
TAPE = """
SELECT
    r.as_of,
    pm.opinion                       AS proposed_action,
    (pm.raw_output->>'quantity')::int AS proposed_qty,
    (d.technicals_snapshot->>'current_price')::numeric AS price
FROM decisions d
JOIN runs r ON d.run_id = r.id
JOIN agent_opinions pm
    ON pm.decision_id = d.id AND pm.agent_name = 'portfolio_manager'
WHERE r.backtest_id = %s
  AND d.symbol = %s
  AND d.technicals_snapshot->>'current_price' IS NOT NULL
ORDER BY r.as_of
"""

RECORDED = """
SELECT
    COUNT(*) FILTER (WHERE status = 'CLOSED') AS closed,
    COUNT(*) FILTER (WHERE status = 'OPEN')   AS open_lots,
    COALESCE(SUM(realized_pnl) FILTER (WHERE status = 'CLOSED'), 0) AS realized,
    COALESCE(SUM(quantity * entry_price) FILTER (WHERE status = 'OPEN'), 0)
        AS open_cost,
    COALESCE(SUM(quantity) FILTER (WHERE status = 'OPEN'), 0) AS open_qty
FROM backtest_outcomes
WHERE backtest_id = %s AND symbol = %s
"""

SYMBOLS = """
SELECT DISTINCT symbol FROM decisions d
JOIN runs r ON d.run_id = r.id WHERE r.backtest_id = %s
"""


@dataclass
class Lot:
    quantity: int
    entry_price: Decimal


@dataclass
class Result:
    """One rule's outcome. `mark_to_market` is the only number worth
    comparing across rules — realized P&L alone flatters any rule that
    happens to leave a losing position open at window end (design
    decision 9d)."""

    name: str
    realized: Decimal = Decimal("0")
    closed: int = 0
    open_lots: list[Lot] = field(default_factory=list)
    buys: int = 0
    sells: int = 0
    blocked: int = 0

    def mark_to_market(self, last_price: Decimal) -> Decimal:
        unrealized = sum(
            (last_price - lot.entry_price) * lot.quantity for lot in self.open_lots
        )
        return self.realized + unrealized


def _slip(price: Decimal, action: str, bps: Decimal) -> Decimal:
    factor = bps / Decimal("10000")
    if action == "BUY":
        return price * (Decimal("1") + factor)
    if action == "SELL":
        return price * (Decimal("1") - factor)
    return price


def _avg_entry(lots: list[Lot]) -> Decimal:
    qty = sum(lot.quantity for lot in lots)
    if not qty:
        return Decimal("0")
    return sum(lot.entry_price * lot.quantity for lot in lots) / qty


def blocked_by_spacing(
    today: date,
    last_buy_day: date | None,
    min_days_between_buys: int | None,
) -> bool:
    """Should today's BUY be refused because the last one was too recent?

    This is the one rule in this file aimed at *stacking* rather than
    drawdown. Backtest 6 opened three lots on 2023-12-19/20/21 — three
    consecutive trading days — and those three lots carry 43% of that
    window's loss. Every drawdown rule tested missed them, because the
    decline across those three days was only ~1.1%, shallower than any
    sane drawdown threshold. Spacing is a different lever: it doesn't ask
    how far price has fallen, only how recently the system last added.

    Returns True to block the buy, False to allow it.

    Three decisions worth naming, since each changes the answer:

    - The gap is counted in TRADING days, not calendar days. That matches
      how the backtest itself defines a day (`_trading_days()` in
      app/agent/backtest.py walks weekdays), and it stops the rule from
      being silently toothless across weekends: a 3-calendar-day gap
      spanning Fri->Mon is one trading day, so a calendar version of this
      rule would have let the Dec 19/20/21 cluster through untouched.
      Holidays are ignored here for the same reason the simulation ignores
      them — matching the simulation matters more than matching the NYSE.
    - The FIRST buy of a position is always allowed. Spacing constrains
      how fast a position is built, not whether one may be opened at all;
      blocking the opener would make this a "trade less" rule rather than
      a "stack slower" one, and those are different hypotheses.
    - `elapsed < min` rather than `<=`: with min=3, a buy three trading
      days after the last one is allowed. Reading "minimum 3 days between
      buys" as permitting a 3-day gap is the least surprising choice.
    """
    if min_days_between_buys is None or last_buy_day is None:
        return False

    elapsed = sum(
        1
        for i in range(1, (today - last_buy_day).days + 1)
        if (last_buy_day + timedelta(days=i)).weekday() < 5
    )
    return elapsed < min_days_between_buys


def _sell_fifo(lots: list[Lot], quantity: int, price: Decimal) -> tuple[Decimal, int]:
    """Oldest lot first, splitting partial lots — the same algorithm as
    `_record_backtest_trade`, in memory. Returns (realized, lots closed)."""
    remaining = quantity
    realized = Decimal("0")
    closed = 0
    while remaining > 0 and lots:
        lot = lots[0]
        close_qty = min(remaining, lot.quantity)
        realized += (price - lot.entry_price) * close_qty
        if close_qty == lot.quantity:
            lots.pop(0)
            closed += 1
        else:
            lot.quantity -= close_qty
            closed += 1
        remaining -= close_qty
    return realized, closed


def replay(
    tape: list[dict],
    *,
    name: str,
    slippage_bps: Decimal,
    max_drawdown_pct: Decimal | None = None,
    stop_loss_pct: Decimal | None = None,
    trailing_stop_pct: Decimal | None = None,
    min_days_between_buys: int | None = None,
) -> Result:
    """Re-gate each recorded Portfolio Manager proposal.

    With all three optional rules left as None this is EXACTLY the
    existing Risk Manager: a flat MAX_POSITION_QTY cap plus
    can't-sell-what-you-don't-hold. That case is the correctness check.

    - max_drawdown_pct: refuse to add to a position already down this much
      against its average entry. Targets the "kept buying into a decline"
      pattern directly.
    - stop_loss_pct: force a full close when the position is down this
      much against its average entry.
    - trailing_stop_pct: force a full close when price falls this far from
      the highest price seen since the position was opened.
    - min_days_between_buys: refuse to open a new lot within this many days
      of the last one. Targets stacking rather than drawdown — see
      blocked_by_spacing().
    """
    res = Result(name=name)
    lots: list[Lot] = []
    peak_since_open: Decimal | None = None
    last_buy_day: date | None = None

    for row in tape:
        price = row["price"]
        action = row["proposed_action"]
        qty = row["proposed_qty"] or 0
        held = sum(lot.quantity for lot in lots)

        if held:
            peak_since_open = price if peak_since_open is None else max(peak_since_open, price)
        else:
            peak_since_open = None

        # --- forced exits are evaluated before the day's proposal: a stop
        # is not an opinion, it fires on price regardless of what the
        # Portfolio Manager wanted to do that day.
        if held and (stop_loss_pct is not None or trailing_stop_pct is not None):
            avg = _avg_entry(lots)
            hit = False
            if stop_loss_pct is not None and avg > 0:
                if (avg - price) / avg * 100 >= stop_loss_pct:
                    hit = True
            if trailing_stop_pct is not None and peak_since_open:
                if (peak_since_open - price) / peak_since_open * 100 >= trailing_stop_pct:
                    hit = True
            if hit:
                fill = _slip(price, "SELL", slippage_bps)
                realized, closed = _sell_fifo(lots, held, fill)
                res.realized += realized - COMMISSION
                res.closed += closed
                res.sells += 1
                held = 0
                peak_since_open = None

        if action == "HOLD" or qty <= 0:
            continue

        if action == "BUY":
            if held >= MAX_POSITION_QTY:
                res.blocked += 1
                continue
            if max_drawdown_pct is not None and held:
                avg = _avg_entry(lots)
                if avg > 0 and (avg - price) / avg * 100 >= max_drawdown_pct:
                    res.blocked += 1
                    continue
            if blocked_by_spacing(
                row["as_of"].date(), last_buy_day, min_days_between_buys
            ):
                res.blocked += 1
                continue
            allowed = min(qty, MAX_POSITION_QTY - held)
            fill = _slip(price, "BUY", slippage_bps)
            lots.append(Lot(quantity=allowed, entry_price=fill))
            last_buy_day = row["as_of"].date()
            res.buys += 1

        elif action == "SELL":
            if held == 0:
                res.blocked += 1
                continue
            allowed = min(qty, held)
            fill = _slip(price, "SELL", slippage_bps)
            realized, closed = _sell_fifo(lots, allowed, fill)
            res.realized += realized - COMMISSION
            res.closed += closed
            res.sells += 1

    res.open_lots = lots
    return res


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python scripts/replay_risk_rules.py <backtest_id>")
    backtest_id = int(sys.argv[1])

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(SYMBOLS, (backtest_id,))
            symbols = [r["symbol"] for r in cur.fetchall()]
            if len(symbols) != 1:
                raise SystemExit(
                    f"backtest {backtest_id} covers {symbols} — this replay "
                    "assumes a single symbol."
                )
            symbol = symbols[0]

            cur.execute(TAPE, (backtest_id, symbol))
            tape = cur.fetchall()

            cur.execute(RECORDED, (backtest_id, symbol))
            recorded = cur.fetchone()

    if not tape:
        raise SystemExit(f"No usable decision tape for backtest {backtest_id}.")

    first_price = tape[0]["price"]
    last_price = tape[-1]["price"]
    print(
        f"backtest {backtest_id}  symbol {symbol}  "
        f"{tape[0]['as_of'].date()} .. {tape[-1]['as_of'].date()}  "
        f"({len(tape)} decision days)"
    )
    print(f"price {first_price:.2f} -> {last_price:.2f}\n")

    # --- correctness gate -------------------------------------------------
    baseline = replay(tape, name="baseline (current rules)", slippage_bps=DEFAULT_SLIPPAGE_BPS)
    recorded_open_unrealized = (
        last_price * recorded["open_qty"] - recorded["open_cost"]
        if recorded["open_qty"]
        else Decimal("0")
    )
    recorded_mtm = recorded["realized"] + recorded_open_unrealized
    replayed_mtm = baseline.mark_to_market(last_price)

    print("=== replay check (must match the recorded run) ===")
    print(f"  recorded: closed={recorded['closed']:<3} open={recorded['open_lots']:<3} "
          f"realized={recorded['realized']:>10.2f}  mark-to-market={recorded_mtm:>10.2f}")
    print(f"  replayed: closed={baseline.closed:<3} open={len(baseline.open_lots):<3} "
          f"realized={baseline.realized:>10.2f}  mark-to-market={replayed_mtm:>10.2f}")

    drift = abs(replayed_mtm - recorded_mtm)
    if drift > Decimal("0.05"):
        print(
            f"\n  MISMATCH ({drift:.2f}). The replay does not reproduce the "
            "recorded run, so nothing below would be trustworthy. Stopping."
        )
        raise SystemExit(1)
    print(f"  match (drift {drift:.4f})\n")

    # --- alternative rules ------------------------------------------------
    variants = [
        baseline,
        replay(tape, name="no adding when down 2%", slippage_bps=DEFAULT_SLIPPAGE_BPS,
               max_drawdown_pct=Decimal("2")),
        replay(tape, name="no adding when down 1%", slippage_bps=DEFAULT_SLIPPAGE_BPS,
               max_drawdown_pct=Decimal("1")),
        replay(tape, name="stop-loss 5%", slippage_bps=DEFAULT_SLIPPAGE_BPS,
               stop_loss_pct=Decimal("5")),
        replay(tape, name="stop-loss 3%", slippage_bps=DEFAULT_SLIPPAGE_BPS,
               stop_loss_pct=Decimal("3")),
        replay(tape, name="trailing stop 5%", slippage_bps=DEFAULT_SLIPPAGE_BPS,
               trailing_stop_pct=Decimal("5")),
        replay(tape, name="no-add 2% + stop 5%", slippage_bps=DEFAULT_SLIPPAGE_BPS,
               max_drawdown_pct=Decimal("2"), stop_loss_pct=Decimal("5")),
        replay(tape, name="min 3 days between buys", slippage_bps=DEFAULT_SLIPPAGE_BPS,
               min_days_between_buys=3),
        replay(tape, name="min 10 days between buys", slippage_bps=DEFAULT_SLIPPAGE_BPS,
               min_days_between_buys=10),
    ]

    buy_hold = (last_price - first_price) * MAX_POSITION_QTY

    print(f"=== mark-to-market by rule (buy & hold {MAX_POSITION_QTY}sh: {buy_hold:+.2f}) ===")
    header = f"  {'rule':<26} {'MtM':>10} {'vs base':>9} {'closed':>7} {'open':>5} {'buys':>5} {'sells':>6} {'blocked':>8}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    base_mtm = baseline.mark_to_market(last_price)
    for r in variants:
        mtm = r.mark_to_market(last_price)
        print(
            f"  {r.name:<26} {mtm:>+10.2f} {mtm - base_mtm:>+9.2f} "
            f"{r.closed:>7} {len(r.open_lots):>5} {r.buys:>5} {r.sells:>6} {r.blocked:>8}"
        )

    print(
        "\nIn-sample by construction. These rules were chosen after seeing "
        "this window's losses, on one symbol. A row that beats the baseline "
        "here has been SELECTED, not validated — the only honest use of this "
        "table is to pick one hypothesis to test on a window it wasn't fitted "
        "to."
    )


if __name__ == "__main__":
    main()
