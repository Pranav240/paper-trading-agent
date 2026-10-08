"""
Persistence for the risk manager's VaR forecasts (table: var_forecasts,
db/migrations/006_var_forecasts.sql). Hand-written SQL, like the rest.

Two steps, deliberately separate in time:

1. record_var_forecast -- at decision time, next to the agent_opinions
   insert. Uses only what the risk manager already computed.
2. fill_realized_returns -- afterwards, once day D's close exists. The
   forecast for D predicts close(D) / close(D-1) - 1; close(D-1) was
   stored as last_close at forecast time, so the realized return needs
   only close(D). Days with no bar (market holidays) stay NULL and are
   left out of any breach count.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from psycopg import AsyncConnection

from app.agent.data_sources import NEW_YORK, PriceDataSource


def position_after(current_qty: int, action: str, quantity: int) -> int:
    if action == "BUY":
        return current_qty + quantity
    if action == "SELL":
        return current_qty - quantity
    return current_qty


async def record_var_forecast(
    conn: AsyncConnection,
    *,
    decision_id: int,
    backtest_id: int | None,
    symbol: str,
    as_of: datetime,
    risk_raw_output: dict,
    final_action: str,
    final_quantity: int,
) -> bool:
    """Insert one forecast row. Returns False (and writes nothing) when the
    risk manager had no VaR -- no price source, or too little history."""
    raw = risk_raw_output
    if raw.get("var_95") is None:
        return False
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO var_forecasts
                (decision_id, backtest_id, symbol, as_of, confidence, window_size,
                 var, cvar, parametric_var, parametric_cvar, var_budget,
                 var_max_qty, position_qty, last_close, risk_flags)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                decision_id,
                backtest_id,
                symbol,
                as_of,
                raw["var_confidence"],
                raw["var_window"],
                raw["var_95"],
                raw["cvar_95"],
                raw["parametric_var_95"],
                raw["parametric_cvar_95"],
                raw["var_budget"],
                raw["var_max_qty"],
                position_after(raw["current_qty"], final_action, final_quantity),
                raw["last_close"],
                raw.get("risk_flags", []),
            ),
        )
    return True


async def fill_realized_returns(
    conn: AsyncConnection,
    price_source: PriceDataSource,
    *,
    backtest_id: int | None,
) -> int:
    """Fill realized_return for every forecast in this backtest (or, with
    backtest_id None, every live forecast) whose day has since closed.
    Returns the number of rows filled."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, symbol, as_of, last_close
            FROM var_forecasts
            WHERE backtest_id IS NOT DISTINCT FROM %s AND realized_return IS NULL
            ORDER BY symbol, as_of
            """,
            (backtest_id,),
        )
        rows = await cur.fetchall()
    if not rows:
        return 0

    by_symbol: dict[str, list[tuple]] = {}
    for row in rows:
        by_symbol.setdefault(row[1], []).append(row)

    filled = 0
    for symbol, symbol_rows in by_symbol.items():
        first, last = symbol_rows[0][2], symbol_rows[-1][2]
        # Ask for bars through the end of the last forecast day; the
        # session-close filter drops any day that hasn't closed yet.
        end = last + timedelta(days=1)
        bars = await price_source.get_recent_bars(
            symbol, end, lookback_days=(end - first).days + 7
        )
        close_on = {b.timestamp.astimezone(NEW_YORK).date(): b.close for b in bars}
        async with conn.cursor() as cur:
            for forecast_id, _, as_of, last_close in symbol_rows:
                close = close_on.get(as_of.astimezone(NEW_YORK).date())
                if close is None:
                    continue
                await cur.execute(
                    "UPDATE var_forecasts SET realized_return = %s WHERE id = %s",
                    (close / last_close - 1, forecast_id),
                )
                filled += 1
    return filled
