"""
Backtest execution — the honest-evaluation gate the project's own rules
require before any V2 work begins (see docs/backtesting-plan.md and the
non-negotiable "no edge found is a legitimate outcome" constraint).

This deliberately does NOT reuse app/agent/runner.py's trade-execution
functions. `_record_paper_trade` / `_record_price_snapshot` write into
`outcomes` / `price_snapshots`, and the `positions` view
(002_positions_view.sql) reads those two tables directly with no
mode/backtest_id filter at all — a backtest fill written there would
show up as a real open position, and a simulated historical price would
be eligible to win "most recent price," the next time GET /positions is
called. So backtest fills get their own ledger, `backtest_outcomes`
(005_backtest_outcomes.sql), and their own risk-check repository,
BacktestRepository (app/repository/backtest.py) — structurally isolated,
not "don't forget to filter." `runs` / `decisions` / `agent_opinions`
ARE shared with the live path (tagged via runs.mode/backtest_id,
003_backtest_support.sql), since nothing reads those without going
through a specific run_id, so mixing rows there is safe.

Known open question, not yet resolved (flagging honestly rather than
assuming it away): AlpacaPriceSource's IEX feed (see
app/agent/data_sources.py's DataFeed.IEX comment) is IEX-exchange-only
data, and IEX itself only launched in 2016. The backtesting plan's
in-sample window starts at 2015 — whether IEX has usable daily bars that
far back, for the symbols this project trades, hasn't been checked
empirically yet. If it doesn't, `_trading_days` below will still
generate those dates, but the Technical Analyst will fall back to its
"not enough bars" HOLD path (see technical_analyst.py) rather than fail
loudly, which could quietly narrow the effective in-sample window
without anyone noticing. Worth a real check before trusting 2015-2016
results specifically.

Trading-day loop: this walks calendar days from window_start to
window_end and skips Saturday/Sunday only. It does NOT know about market
holidays (Thanksgiving, Christmas, etc.) — a real trading calendar
(e.g. the `pandas_market_calendars` package) would be a fair follow-up,
but for V1 the cost of running (and getting a HOLD/no-data result on) a
handful of holiday dates is a wasted-cycle nit, not a correctness bug
that would flip a "no edge" result into a false positive.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.agent.graph import CompiledStateGraph
from app.models import Action

# Applied against the decision-time price to approximate the cost of
# actually getting a fill, instead of assuming perfect, instant,
# frictionless execution — docs/backtesting-plan.md's slippage section
# explicitly allows this basis-point approach as an alternative to
# fetching the next bar's open (which would need a second price-source
# call per symbol per day for comparatively little extra realism at V1
# scale). BUY fills worse (higher) than the quoted price, SELL fills
# worse (lower). 5 bps (0.05%) is a small, deliberately conservative
# default for a liquid large-cap like AAPL — a placeholder assumption,
# not a researched constant, and worth sensitivity-checking once real
# results exist.
DEFAULT_SLIPPAGE_BPS = Decimal("5")

# Alpaca charges $0 commission on stock trades (paper and live) — modeled
# as zero here explicitly, per the plan's note that this should be a
# stated assumption, not something silently ignored.
COMMISSION = Decimal("0")


def _apply_slippage(price: Decimal, action: Action, bps: Decimal) -> Decimal:
    factor = bps / Decimal("10000")
    if action == Action.BUY:
        return price * (Decimal("1") + factor)
    if action == Action.SELL:
        return price * (Decimal("1") - factor)
    return price


def _trading_days(window_start: date, window_end: date) -> list[date]:
    days = []
    current = window_start
    while current <= window_end:
        if current.weekday() < 5:  # Monday=0 .. Sunday=6
            days.append(current)
        current += timedelta(days=1)
    return days


async def _record_backtest_trade(
    conn: psycopg.AsyncConnection,
    *,
    backtest_id: int,
    decision_id: int,
    symbol: str,
    action: Action,
    quantity: int,
    price: Decimal,
    as_of: datetime,
) -> None:
    """FIFO lot accounting against backtest_outcomes — same algorithm as
    runner.py's _record_paper_trade (oldest-open-lot-first, partial-lot
    splitting), deliberately re-implemented rather than shared, because
    it writes to a different table with different scoping (backtest_id)
    and threading a "which table" flag through one shared function would
    read worse than two short, table-specific ones.
    """
    if action == Action.HOLD or quantity <= 0:
        return

    if action == Action.BUY:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO backtest_outcomes
                    (backtest_id, decision_id, symbol, quantity, entry_price,
                     opened_at, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'OPEN')
                """,
                (backtest_id, decision_id, symbol, quantity, price, as_of),
            )
        return

    # action == Action.SELL
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT id, quantity, entry_price, opened_at FROM backtest_outcomes "
            "WHERE backtest_id = %s AND symbol = %s AND status = 'OPEN' "
            "ORDER BY opened_at ASC",
            (backtest_id, symbol),
        )
        open_lots = await cur.fetchall()

    total_open = sum(lot["quantity"] for lot in open_lots)
    if quantity > total_open:
        raise ValueError(
            f"Backtest SELL {quantity} {symbol} exceeds open position "
            f"({total_open}) — risk_manager should have vetoed/scaled this "
            "before it reached trade execution."
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
                    "UPDATE backtest_outcomes SET status = 'CLOSED', "
                    "exit_price = %s, closed_at = %s, realized_pnl = %s "
                    "WHERE id = %s",
                    (price, as_of, realized_pnl, lot["id"]),
                )
            else:
                await cur.execute(
                    "UPDATE backtest_outcomes SET quantity = quantity - %s "
                    "WHERE id = %s",
                    (close_qty, lot["id"]),
                )
                await cur.execute(
                    """
                    INSERT INTO backtest_outcomes
                        (backtest_id, decision_id, symbol, quantity, entry_price,
                         opened_at, status, exit_price, closed_at, realized_pnl)
                    VALUES (%s, %s, %s, %s, %s, %s, 'CLOSED', %s, %s, %s)
                    """,
                    (
                        backtest_id,
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


async def run_backtest(
    pool: AsyncConnectionPool,
    graph_factory: Callable[[int], CompiledStateGraph],
    *,
    name: str,
    symbols: list[str],
    window_start: date,
    window_end: date,
    slippage_bps: Decimal = DEFAULT_SLIPPAGE_BPS,
    config: dict | None = None,
) -> int:
    """Runs one full backtest: one simulated decision cycle per trading
    day per symbol across [window_start, window_end], all persisted
    under one `backtests` row (id returned).

    `graph_factory` builds the graph, given the backtest_id this run just
    got assigned — a callback rather than a pre-built graph, because of a
    real chicken-and-egg problem: the graph's risk_manager node needs a
    BacktestRepository scoped to THIS backtest's id (see
    app/repository/backtest.py), and that id doesn't exist until the
    `backtests` row below is inserted. A caller can't build a
    fully-wired graph before calling this function; it can only build
    one once handed the id, hence the callback instead of a plain
    argument. Typical caller:

        def make_graph(backtest_id: int) -> CompiledStateGraph:
            price_source, headline_source = build_historical_data_sources(pool)
            return build_decision_graph(
                price_source=price_source,
                headline_source=headline_source,
                repository=BacktestRepository(pool, backtest_id),
            )
        backtest_id = await run_backtest(pool, make_graph, name=..., ...)
    """
    trading_days = _trading_days(window_start, window_end)

    async with pool.connection() as conn:
        # This insert must commit for real before the day loop starts —
        # a REAL bug, caught only by a live pilot run, not by testing:
        # without this explicit transaction block, executing it directly
        # on `conn` (non-autocommit by default) opens an implicit
        # transaction that's never closed. Every subsequent
        # `async with conn.transaction():` below then finds itself
        # already inside an open transaction and downgrades to a
        # SAVEPOINT instead of a real BEGIN/COMMIT — so no day's writes
        # become visible to any OTHER connection (including
        # BacktestRepository.list_positions()'s own connection) until
        # the whole function finally returns this connection to the
        # pool. Symptom: risk_manager saw current_qty=0 on every single
        # day of a real pilot run, so the position cap never engaged —
        # 25 BUYs over one quarter, 93 shares total, no VETO/SCALE ever
        # fired. Wrapping this insert in its own transaction closes that
        # window: it's fully committed before any node ever reads
        # positions.
        async with conn.transaction():
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    INSERT INTO backtests (name, window_start, window_end, config)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (name, window_start, window_end, psycopg.types.json.Json(config or {})),
                )
                backtest_id = (await cur.fetchone())["id"]

        graph = graph_factory(backtest_id)

        for day in trading_days:
            # Midday UTC, not midnight — purely so this sits visibly
            # between the prior day's close and this day's open when
            # eyeballing raw rows. Has no effect on what data gets
            # pulled: both HistoricalHeadlineSource and AlpacaPriceSource
            # do their own day-boundary math off `as_of`.
            as_of = datetime.combine(
                day, datetime.min.time(), tzinfo=timezone.utc
            ) + timedelta(hours=12)

            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        """
                        INSERT INTO runs (status, started_at, as_of, mode, backtest_id)
                        VALUES ('RUNNING', %s, %s, 'BACKTEST', %s)
                        RETURNING id
                        """,
                        (datetime.now(timezone.utc), as_of, backtest_id),
                    )
                    run_id = (await cur.fetchone())["id"]

                for symbol in symbols:
                    result = await graph.ainvoke({"symbol": symbol, "as_of": as_of})

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
                            RETURNING id
                            """,
                            (
                                run_id,
                                symbol,
                                final_action,
                                portfolio_opinion.confidence or 0.5,
                                risk_opinion.reasoning,
                                psycopg.types.json.Json(technical.raw_output),
                                psycopg.types.json.Json(sentiment.raw_output),
                            ),
                        )
                        decision_id = (await cur.fetchone())["id"]

                    async with conn.cursor() as cur:
                        for op in (
                            technical,
                            sentiment,
                            portfolio_opinion,
                            risk_opinion,
                        ):
                            await cur.execute(
                                """
                                INSERT INTO agent_opinions
                                    (decision_id, agent_name, opinion, confidence,
                                     reasoning, raw_output)
                                VALUES (%s, %s, %s, %s, %s, %s)
                                """,
                                (
                                    decision_id,
                                    op.agent_name,
                                    op.opinion,
                                    op.confidence,
                                    op.reasoning,
                                    psycopg.types.json.Json(op.raw_output),
                                ),
                            )

                    # Same fallback as runner.py: the Technical Analyst's
                    # raw_output is the only price source here (backtests
                    # never write price_snapshots — see this module's
                    # docstring), so a HOLD from insufficient bar history
                    # means no current_price key, and the trade — if any
                    # was somehow still proposed — silently isn't priced
                    # or recorded. Matches live-runner behavior; not a
                    # new gap introduced here.
                    raw_price = technical.raw_output.get("current_price")
                    if raw_price is not None and final_quantity:
                        decision_price = Decimal(str(raw_price))
                        fill_price = _apply_slippage(
                            decision_price, Action(final_action), slippage_bps
                        )
                        await _record_backtest_trade(
                            conn,
                            backtest_id=backtest_id,
                            decision_id=decision_id,
                            symbol=symbol,
                            action=Action(final_action),
                            quantity=final_quantity,
                            price=fill_price,
                            as_of=as_of,
                        )

                async with conn.cursor() as cur:
                    await cur.execute(
                        "UPDATE runs SET status = 'SUCCESS', finished_at = %s "
                        "WHERE id = %s",
                        (datetime.now(timezone.utc), run_id),
                    )

        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE backtests SET status = 'SUCCESS', finished_at = %s "
                "WHERE id = %s",
                (datetime.now(timezone.utc), backtest_id),
            )

    return backtest_id


async def compute_backtest_metrics(pool: AsyncConnectionPool, backtest_id: int) -> dict:
    """Aggregates backtest_outcomes into the numbers
    docs/backtesting-plan.md asks for: realized P&L, trade count, win
    rate. Deliberately does NOT compute a buy-and-hold comparison here —
    that needs a "price on day 1 vs. price on day N" lookup the caller
    already has a PriceDataSource for, so building it into this function
    would mean either duplicating that fetch or coupling this function to
    PriceDataSource for no real benefit; the caller composes the two.

    `realized_pnl` only counts CLOSED lots. `open_trades_at_window_end`
    is reported alongside it explicitly, as a flag that the number isn't
    a complete picture, not swept under the rug — any lot still open
    when the backtest window ends is real, unrealized exposure this
    function doesn't try to mark-to-market.
    """
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'CLOSED') AS closed_trades,
                    COUNT(*) FILTER (WHERE status = 'OPEN') AS open_trades,
                    COALESCE(
                        SUM(realized_pnl) FILTER (WHERE status = 'CLOSED'), 0
                    ) AS realized_pnl,
                    COUNT(*) FILTER (
                        WHERE status = 'CLOSED' AND realized_pnl > 0
                    ) AS winning_trades
                FROM backtest_outcomes
                WHERE backtest_id = %s
                """,
                (backtest_id,),
            )
            row = await cur.fetchone()

    closed = row["closed_trades"]
    return {
        "backtest_id": backtest_id,
        "closed_trades": closed,
        "open_trades_at_window_end": row["open_trades"],
        "realized_pnl": row["realized_pnl"],
        "win_rate": (row["winning_trades"] / closed) if closed else None,
    }
