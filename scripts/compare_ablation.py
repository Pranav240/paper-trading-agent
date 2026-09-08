"""
Compare the no-sentiment ablation run against the equivalent full run.

Realized P&L alone is misleading whenever lots are still OPEN at window
end (design decision 9d) -- this computes mark-to-market for both runs
using the last price snapshot inside each run's own window.

Run:  python scripts/compare_ablation.py 4 7
"""

import os
import sys

import psycopg
from dotenv import load_dotenv

load_dotenv()

SUMMARY = """
SELECT
    b.id,
    b.name,
    b.window_start,
    b.window_end,
    COUNT(*) FILTER (WHERE o.status = 'CLOSED')            AS closed,
    COUNT(*) FILTER (WHERE o.status = 'OPEN')              AS open_lots,
    COALESCE(SUM(o.realized_pnl) FILTER (WHERE o.status = 'CLOSED'), 0) AS realized
FROM backtests b
LEFT JOIN backtest_outcomes o ON o.backtest_id = b.id
WHERE b.id = %s
GROUP BY b.id, b.name, b.window_start, b.window_end;
"""

OPEN_LOTS = """
SELECT symbol, quantity, entry_price
FROM backtest_outcomes
WHERE backtest_id = %s AND status = 'OPEN';
"""

# `decisions` has no price column -- the price the agent saw that day is
# inside technicals_snapshot (JSONB). Key name varies, so pull the whole
# blob and hunt for a price-like numeric field, with price_snapshots as a
# fallback.
LAST_SNAPSHOT = """
SELECT r.as_of, d.technicals_snapshot
FROM decisions d
JOIN runs r ON d.run_id = r.id
WHERE r.backtest_id = %s AND d.symbol = %s
ORDER BY r.as_of DESC
LIMIT 1;
"""

FALLBACK_PRICE = """
SELECT captured_at, close
FROM price_snapshots
WHERE symbol = %s AND captured_at::date <= %s
ORDER BY captured_at DESC
LIMIT 1;
"""

PRICE_KEYS = ("price", "close", "last_price", "current_price", "close_price")


def _price_from_snapshot(snap):
    if not isinstance(snap, dict):
        return None
    for key in PRICE_KEYS:
        val = snap.get(key)
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            try:
                return float(val)
            except ValueError:
                pass
    return None

ACTIONS = """
SELECT d.action, COUNT(*)
FROM decisions d
JOIN runs r ON d.run_id = r.id
WHERE r.backtest_id = %s
GROUP BY d.action
ORDER BY COUNT(*) DESC;
"""


def report(cur, bid):
    cur.execute(SUMMARY, (bid,))
    row = cur.fetchone()
    if row is None:
        print(f"backtest {bid}: not found")
        return
    _id, name, start, end, closed, open_lots, realized = row

    mtm = float(realized)
    detail = []
    cur.execute(OPEN_LOTS, (bid,))
    for symbol, qty, entry in cur.fetchall():
        last = None
        when = None

        cur.execute(LAST_SNAPSHOT, (bid, symbol))
        pr = cur.fetchone()
        if pr is not None:
            when, snap = pr
            last = _price_from_snapshot(snap)
            if last is None:
                detail.append(
                    f"    (technicals_snapshot keys: "
                    f"{sorted(snap.keys()) if isinstance(snap, dict) else type(snap)})"
                )

        if last is None:
            cur.execute(FALLBACK_PRICE, (symbol, end))
            pr = cur.fetchone()
            if pr is not None:
                when, last = pr[0], float(pr[1])

        if last is None:
            detail.append(f"    {symbol} x{qty} @ {entry} -- NO PRICE FOUND")
            continue

        unreal = (float(last) - float(entry)) * qty
        mtm += unreal
        stamp = when.date() if hasattr(when, "date") else when
        detail.append(
            f"    {symbol} x{qty} entry {entry} -> {last} ({stamp}) "
            f"= {unreal:+.2f} unrealized"
        )

    print(f"\n=== backtest {bid}: {name}")
    print(f"    window            {start} .. {end}")
    print(f"    closed trades     {closed}")
    print(f"    open at end       {open_lots}")
    print(f"    realized P&L      {float(realized):+.2f}")
    for line in detail:
        print(line)
    print(f"    MARK-TO-MARKET    {mtm:+.2f}")

    cur.execute(ACTIONS, (bid,))
    acts = ", ".join(f"{a}={c}" for a, c in cur.fetchall())
    print(f"    decisions         {acts}")
    return mtm


def main():
    ids = [int(a) for a in sys.argv[1:]] or [4, 7]
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            results = {bid: report(cur, bid) for bid in ids}

    vals = [v for v in results.values() if v is not None]
    if len(vals) == 2:
        a, b = vals
        print(f"\n=== difference (second minus first): {b - a:+.2f}")


if __name__ == "__main__":
    main()
