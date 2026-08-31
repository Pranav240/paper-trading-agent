"""
Turns the compiled LangGraph flow into one full, persisted decision cycle.

This is the file the API actually calls. It owns two jobs the graph
itself deliberately doesn't do:

1. Looping over every active watchlist symbol (the graph runs ONE symbol
   per invocation — `symbol` is a plain state key, not a list — because
   keeping the graph single-symbol keeps every node's logic simple; the
   multi-symbol loop belongs here, one level up).
2. Persisting the result — runs/decisions/agent_opinions rows, and the
   paper-trade execution against `outcomes` — in one Postgres
   transaction, same all-or-nothing guarantee
   PostgresRepository.trigger_run()'s stub already had.

KNOWN GAP (flagging honestly rather than hiding it): `_latest_close`
below reads from `price_snapshots`, but nothing in Phase 03 writes to
that table yet — the Technical Analyst pulls bars straight from Alpaca
into memory, it never persists them. So `_latest_close` will return None
for now, and this function falls back to the Technical Analyst's own
`current_price` (captured in its `raw_output`) to price paper trades.
That fallback is honest but not ideal: it means the `positions` view's
`current_price` will also be NULL until something actually populates
price_snapshots (a natural small addition — write one row per symbol per
run, right after the Technical Analyst node runs). Not fixed here to
avoid silently expanding this file's scope beyond "wire the graph up
and persist its output."
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.agent.graph import CompiledStateGraph
from app.models import Action, Decision, RunResult, RunStatus


async def _active_symbols(conn: psycopg.AsyncConnection) -> list[str]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT symbol FROM watchlist WHERE active = true ORDER BY symbol"
        )
        rows = await cur.fetchall()
    return [row["symbol"] for row in rows]


async def _latest_close(conn: psycopg.AsyncConnection, symbol: str) -> Decimal | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT close FROM price_snapshots WHERE symbol = %s "
            "ORDER BY captured_at DESC LIMIT 1",
            (symbol,),
        )
        row = await cur.fetchone()
    return row["close"] if row else None


async def _record_paper_trade(
    conn: psycopg.AsyncConnection,
    *,
    decision_id: int,
    symbol: str,
    action: Action,
    quantity: int,
    price: Decimal,
    as_of: datetime,
) -> None:
    """Turns a final BUY/SELL/HOLD call into `outcomes` rows.

    - HOLD or quantity == 0: nothing to record.
    - BUY: always opens a NEW row, never merged into an existing open lot.
      Keeping each buy as its own lot (own entry price, own opened_at) is
      what makes the positions view's quantity-weighted average correct,
      and lets SELL close specific lots FIFO instead of guessing at a
      blended cost basis.
    - SELL: closes open lots oldest-first (FIFO) until `quantity` shares
      are accounted for. A partial lot close shrinks the open row and
      inserts a separate CLOSED row for the sold portion, so
      `outcomes.quantity` always means "shares represented by this row"
      for both open and closed rows.

    If quantity exceeds what's actually held, that's risk_manager.py
    failing to catch it (it should always scale/veto first) — this
    raises loudly rather than silently modeling a short position, which
    this system doesn't support.
    """
    if action == Action.HOLD or quantity <= 0:
        return

    if action == Action.BUY:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO outcomes
                    (decision_id, symbol, quantity, entry_price, opened_at, status)
                VALUES (%s, %s, %s, %s, %s, 'OPEN')
                """,
                (decision_id, symbol, quantity, price, as_of),
            )
        return

    # action == Action.SELL
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT id, quantity, entry_price, opened_at FROM outcomes "
            "WHERE symbol = %s AND status = 'OPEN' ORDER BY opened_at ASC",
            (symbol,),
        )
        open_lots = await cur.fetchall()

    total_open = sum(lot["quantity"] for lot in open_lots)
    if quantity > total_open:
        raise ValueError(
            f"SELL {quantity} {symbol} exceeds open position ({total_open}) — "
            "risk_manager should have vetoed or scaled this before it reached "
            "paper-trade execution."
        )

    remaining = quantity
    async with conn.cursor() as cur:
        for lot in open_lots:
            if remaining <= 0:
                break
            close_qty = min(remaining, lot["quantity"])
            realized_pnl = (price - lot["entry_price"]) * close_qty

            if close_qty == lot["quantity"]:
                await cur.execute(
                    "UPDATE outcomes SET status = 'CLOSED', exit_price = %s, "
                    "closed_at = %s, realized_pnl = %s WHERE id = %s",
                    (price, as_of, realized_pnl, lot["id"]),
                )
            else:
                await cur.execute(
                    "UPDATE outcomes SET quantity = quantity - %s WHERE id = %s",
                    (close_qty, lot["id"]),
                )
                await cur.execute(
                    """
                    INSERT INTO outcomes
                        (decision_id, symbol, quantity, entry_price, opened_at,
                         status, exit_price, closed_at, realized_pnl)
                    VALUES (%s, %s, %s, %s, %s, 'CLOSED', %s, %s, %s)
                    """,
                    (
                        decision_id,
                        symbol,
                        close_qty,
                        lot["entry_price"],
                        lot["opened_at"],
                        price,
                        as_of,
                        realized_pnl,
                    ),
                )
            remaining -= close_qty


async def run_decision_cycle(
    pool: AsyncConnectionPool,
    graph: CompiledStateGraph,
) -> RunResult:
    """One full agent cycle across every active watchlist symbol, all in
    one transaction — a failure partway through leaves no partial run
    behind, same guarantee the Phase 02 stub had."""
    started_at = datetime.now(timezone.utc)
    decisions: list[Decision] = []

    async with pool.connection() as conn:
        async with conn.transaction():
            symbols = await _active_symbols(conn)

            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "INSERT INTO runs (status, started_at, as_of) "
                    "VALUES ('RUNNING', %s, %s) RETURNING id",
                    (started_at, started_at),
                )
                run_row = await cur.fetchone()
                run_id = run_row["id"]

            for symbol in symbols:
                result = await graph.ainvoke({"symbol": symbol, "as_of": started_at})

                technical = result["technical_opinion"]
                sentiment = result["sentiment_opinion"]
                portfolio_opinion = result["portfolio_opinion"]
                risk_opinion = result["risk_opinion"]
                final_action = result["final_action"]
                final_quantity = result["final_quantity"]

                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        """
                        INSERT INTO decisions
                            (run_id, symbol, action, confidence, reasoning,
                             technicals_snapshot, sentiment_snapshot)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING id, symbol, action, confidence, reasoning,
                                  technicals_snapshot, sentiment_snapshot, created_at
                        """,
                        (
                            run_id,
                            symbol,
                            final_action,
                            # The final action is risk_manager's call, so its
                            # reasoning is the most accurate "why" for the
                            # decisions row — portfolio_opinion.confidence is
                            # still the best confidence NUMBER we have (risk
                            # opinions don't carry one; they're a gate, not a
                            # forecast).
                            portfolio_opinion.confidence or 0.5,
                            risk_opinion.reasoning,
                            psycopg.types.json.Json(technical.raw_output),
                            psycopg.types.json.Json(sentiment.raw_output),
                        ),
                    )
                    decision_row = await cur.fetchone()
                decision = Decision(**decision_row)
                decisions.append(decision)

                async with conn.cursor() as cur:
                    for op in (technical, sentiment, portfolio_opinion, risk_opinion):
                        await cur.execute(
                            """
                            INSERT INTO agent_opinions
                                (decision_id, agent_name, opinion, confidence,
                                 reasoning, raw_output)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (
                                decision.id,
                                op.agent_name,
                                op.opinion,
                                op.confidence,
                                op.reasoning,
                                psycopg.types.json.Json(op.raw_output),
                            ),
                        )

                price = await _latest_close(conn, symbol)
                if price is None:
                    raw_price = technical.raw_output.get("current_price")
                    price = Decimal(str(raw_price)) if raw_price is not None else None

                if price is not None:
                    await _record_paper_trade(
                        conn,
                        decision_id=decision.id,
                        symbol=symbol,
                        action=Action(final_action),
                        quantity=final_quantity,
                        price=price,
                        as_of=started_at,
                    )

            finished_at = datetime.now(timezone.utc)
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE runs SET status = 'SUCCESS', finished_at = %s WHERE id = %s",
                    (finished_at, run_id),
                )

    return RunResult(
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        status=RunStatus.SUCCESS,
        decisions=decisions,
    )
