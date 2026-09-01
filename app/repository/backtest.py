"""
Backtest-scoped implementation of the Repository protocol.

Why this exists rather than reusing PostgresRepository: risk_manager.py
calls `repository.list_positions(symbol)` mid-cycle to check current
holdings before approving a BUY/SELL. If a backtest run were wired up
with PostgresRepository, that call would return REAL live open
positions — a backtest would gate its simulated trades against actual
paper-trading state, which is both wrong (the two portfolios have
nothing to do with each other) and a one-way door to confusing bugs. This
class answers the exact same question — "what's currently held, for this
one portfolio" — but reads from `backtest_outcomes` scoped to one
`backtest_id` (005_backtest_outcomes.sql) instead of the live `outcomes`
table.

`list_decisions` / `trigger_run` are NOT implemented. Nothing in the
backtest path calls them — the graph and app/agent/backtest.py's runner
only ever call `list_positions` on a Repository — and stubbing them with
a real implementation would be code with no test coverage and no caller,
which is worse than an honest NotImplementedError.
"""

from __future__ import annotations

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.models import Decision, Position, RunResult


class BacktestRepository:
    def __init__(self, pool: AsyncConnectionPool, backtest_id: int) -> None:
        self._pool = pool
        self._backtest_id = backtest_id

    async def list_positions(self, symbol: str | None = None) -> list[Position]:
        # Same quantity-weighted average logic as the `positions` view
        # (002_positions_view.sql), computed here in Python instead of a
        # second SQL view, since this is the only place that needs it and
        # it's already scoped by a backtest_id parameter a view can't take.
        query = (
            "SELECT symbol, quantity, entry_price, opened_at FROM backtest_outcomes "
            "WHERE backtest_id = %s AND status = 'OPEN'"
        )
        params: list[object] = [self._backtest_id]
        if symbol:
            query += " AND symbol = %s"
            params.append(symbol.upper())

        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query, params)
                lots = await cur.fetchall()

        by_symbol: dict[str, list[dict]] = {}
        for lot in lots:
            by_symbol.setdefault(lot["symbol"], []).append(lot)

        positions = []
        for sym, sym_lots in by_symbol.items():
            total_qty = sum(lot["quantity"] for lot in sym_lots)
            if total_qty <= 0:
                continue
            weighted_cost = sum(
                lot["entry_price"] * lot["quantity"] for lot in sym_lots
            )
            positions.append(
                Position(
                    symbol=sym,
                    quantity=total_qty,
                    avg_entry_price=round(weighted_cost / total_qty, 4),
                    # current_price/unrealized_pnl are a live-pricing
                    # concept (see app/models.py's comment on why these
                    # are Optional) — risk_manager only reads .quantity,
                    # so leaving these None is honest, not lazy: a
                    # backtest lot's "current price" during the loop is
                    # whatever the next simulated day decides, not
                    # something this method should guess at.
                    current_price=None,
                    unrealized_pnl=None,
                    opened_at=min(lot["opened_at"] for lot in sym_lots),
                )
            )
        return positions

    async def list_decisions(
        self, symbol: str | None = None, limit: int = 50
    ) -> list[Decision]:
        raise NotImplementedError(
            "BacktestRepository is scoped to run_decision_cycle's "
            "risk_manager.list_positions() call only — nothing in the "
            "backtest path calls list_decisions()."
        )

    async def trigger_run(self) -> RunResult:
        raise NotImplementedError(
            "Backtests are started via app.agent.backtest.run_backtest(), "
            "not via the live Repository.trigger_run() seam."
        )
