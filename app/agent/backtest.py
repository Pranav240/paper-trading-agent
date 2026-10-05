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

Trading-day loop: by default this walks calendar days from window_start
to window_end and skips Saturday/Sunday only, which is how every V1 run
worked -- including 11 market holidays in run #4's window. Callers pass
`trading_days` (scripts/run_backtest.py uses Alpaca's market calendar,
data_sources.alpaca_trading_days) to run real sessions only.

Fills (V2, phase 07): V1 filled every trade at the Technical Analyst's
current_price, which turned out to be day D's own close (the look-ahead
leak). With `execution_prices` given, a trade decided before the open on
D fills at D's open, plus slippage, as docs/backtesting-plan.md
specifies; that open is stored on the decision (decisions.execution_price)
so offline replays fill at the same price. Without it, the V1 rule stands.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Protocol

import psycopg
from langchain_core.callbacks import UsageMetadataCallbackHandler
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.agent.graph import CompiledStateGraph
from app.agent.llm import usage_cost_usd
from app.agent.var_forecasts import record_var_forecast
from app.models import Action

# Applied against the fill price to approximate the cost of actually
# getting a fill, instead of assuming perfect, instant, frictionless
# execution. V1 used this basis-point cost INSTEAD of the next bar's open
# (docs/backtesting-plan.md allows either); with `execution_prices` the
# fill is now at the open AND this still applies on top, as a spread
# cost. BUY fills worse (higher) than the quoted price, SELL fills
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


class ExecutionPrices(Protocol):
    async def open_on(self, symbol: str, day: date) -> Decimal | None: ...


def usage_summary(handler: UsageMetadataCallbackHandler) -> dict:
    """Tokens per model, as plain JSON-able ints."""
    return {
        model: {
            key: usage.get(key, 0)
            for key in ("input_tokens", "output_tokens", "total_tokens")
        }
        for model, usage in handler.usage_metadata.items()
    }


class BudgetExceeded(RuntimeError):
    """A run stopped itself at its spending cap (see max_cost_usd)."""


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


def _add_usage(a: dict | None, b: dict) -> dict:
    total = {model: dict(counts) for model, counts in (a or {}).items()}
    for model, counts in b.items():
        slot = total.setdefault(model, {})
        for key, value in counts.items():
            slot[key] = slot.get(key, 0) + value
    return total


async def _finish(
    conn: psycopg.AsyncConnection,
    backtest_id: int,
    status: str,
    usage: UsageMetadataCallbackHandler,
) -> None:
    """Set the final status and add this process's tokens to any already
    recorded (a resumed run keeps the earlier part's usage)."""
    async with conn.cursor() as cur:
        await cur.execute("SELECT llm_usage FROM backtests WHERE id = %s", (backtest_id,))
        previous = (await cur.fetchone())[0]
        await cur.execute(
            "UPDATE backtests SET status = %s, finished_at = %s, llm_usage = %s "
            "WHERE id = %s AND status = 'RUNNING'",
            (
                status,
                datetime.now(timezone.utc),
                psycopg.types.json.Json(_add_usage(previous, usage_summary(usage))),
                backtest_id,
            ),
        )


async def _mark_failed(
    pool: AsyncConnectionPool, backtest_id: int, usage: UsageMetadataCallbackHandler
) -> None:
    """Fresh connection: the run's own one may be the thing that broke."""
    async with pool.connection() as conn:
        await _finish(conn, backtest_id, "FAILED", usage)


# Settings a resumed run must share with the run it continues; anything
# else (git commit, dirty flag) may differ and is recorded per resume.
RESUME_MUST_MATCH = (
    "symbols", "llm", "models", "sentiment_mode", "no_sentiment_ablation",
    "risk_node", "slippage_bps", "fill", "calendar",
)


async def _start_resume(
    cur, backtest_id: int, window_start: date, window_end: date, config: dict
) -> tuple[int, date]:
    """Validate that `backtest_id` can be continued with this config, mark
    it RUNNING again, and return (id, last completed day)."""
    await cur.execute(
        "SELECT window_start, window_end, status, config, llm_usage "
        "FROM backtests WHERE id = %s FOR UPDATE",
        (backtest_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise ValueError(f"No backtest {backtest_id} to resume.")
    if row["status"] not in ("RUNNING", "FAILED"):
        raise ValueError(f"Backtest {backtest_id} is {row['status']}; only an unfinished run resumes.")
    if (row["window_start"], row["window_end"]) != (window_start, window_end):
        raise ValueError(
            f"Window {window_start}..{window_end} does not match backtest "
            f"{backtest_id}'s {row['window_start']}..{row['window_end']}."
        )
    stored = row["config"] or {}
    mismatched = [k for k in RESUME_MUST_MATCH if stored.get(k) != config.get(k)]
    if mismatched:
        raise ValueError(f"Cannot resume backtest {backtest_id}: settings differ: {mismatched}")

    await cur.execute(
        "SELECT count(*) FILTER (WHERE status <> 'SUCCESS') AS unfinished, max(as_of) AS last "
        "FROM runs WHERE backtest_id = %s",
        (backtest_id,),
    )
    runs = await cur.fetchone()
    if runs["unfinished"]:
        raise ValueError(f"Backtest {backtest_id} has a partly written day; inspect before resuming.")
    last_done = runs["last"].date() if runs["last"] else date.min

    resume = {
        "after_day": str(last_done),
        "at": datetime.now(timezone.utc).isoformat(),
        "git_commit": config.get("git_commit"),
        "git_dirty": config.get("git_dirty"),
        # None: the earlier part's tokens were never recorded (the process
        # was killed outright), so llm_usage covers only what came after.
        "llm_usage_before": row["llm_usage"],
    }
    await cur.execute(
        "UPDATE backtests SET status = 'RUNNING', finished_at = NULL, "
        "config = jsonb_set(config, '{resumes}', COALESCE(config->'resumes', '[]'::jsonb) || %s::jsonb) "
        "WHERE id = %s",
        (psycopg.types.json.Json([resume]), backtest_id),
    )
    return backtest_id, last_done


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
    trading_days: list[date] | None = None,
    execution_prices: ExecutionPrices | None = None,
    resume_backtest_id: int | None = None,
    max_cost_usd: float | None = None,
) -> int:
    """Runs one full backtest (see _run_backtest). If it raises -- an API
    out of credits, a network drop, Ctrl-C -- the `backtests` row is marked
    FAILED with the tokens used so far, instead of being left RUNNING
    forever as backtests 5 and 18 were. The exception still propagates.

    `resume_backtest_id` continues an interrupted run after its last
    completed day instead of starting a new one (see _start_resume). That
    is sound because a run's only cross-day state -- open positions --
    lives in backtest_outcomes, and each day commits as one transaction,
    so a killed run leaves whole days or nothing.

    `max_cost_usd` caps what THIS process spends on LLM calls (priced by
    app/agent/llm.py); the run stops before a day that would likely cross
    it, raising BudgetExceeded, and can be resumed later."""
    usage = UsageMetadataCallbackHandler()
    created: list[int] = []
    try:
        return await _run_backtest(
            pool,
            graph_factory,
            name=name,
            symbols=symbols,
            window_start=window_start,
            window_end=window_end,
            slippage_bps=slippage_bps,
            config=config,
            trading_days=trading_days,
            execution_prices=execution_prices,
            usage=usage,
            created=created,
            resume_backtest_id=resume_backtest_id,
            max_cost_usd=max_cost_usd,
        )
    except BaseException:
        if created:
            await _mark_failed(pool, created[0], usage)
        raise


async def _run_backtest(
    pool: AsyncConnectionPool,
    graph_factory: Callable[[int], CompiledStateGraph],
    *,
    name: str,
    symbols: list[str],
    window_start: date,
    window_end: date,
    slippage_bps: Decimal,
    config: dict | None,
    trading_days: list[date] | None,
    execution_prices: ExecutionPrices | None,
    usage: UsageMetadataCallbackHandler,
    created: list[int],
    resume_backtest_id: int | None = None,
    max_cost_usd: float | None = None,
) -> int:
    """Runs one full backtest: one simulated decision cycle per trading
    day per symbol across [window_start, window_end], all persisted
    under one `backtests` row (id returned).

    Every graph call carries `usage` as a LangChain callback, so token
    counts per model are summed into backtests.llm_usage.

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
    if trading_days is None:
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
                if resume_backtest_id is None:
                    await cur.execute(
                        """
                        INSERT INTO backtests (name, window_start, window_end, config)
                        VALUES (%s, %s, %s, %s)
                        RETURNING id
                        """,
                        (name, window_start, window_end, psycopg.types.json.Json(config or {})),
                    )
                    backtest_id = (await cur.fetchone())["id"]
                else:
                    backtest_id, last_done = await _start_resume(
                        cur, resume_backtest_id, window_start, window_end, config or {}
                    )
                    trading_days = [d for d in trading_days if d > last_done]
        created.append(backtest_id)

        graph = graph_factory(backtest_id)

        for done, day in enumerate(trading_days):
            if max_cost_usd is not None and done:
                # Stop BEFORE a day that would likely cross the cap: spent
                # so far plus this run's average cost per day. The run is
                # marked FAILED and can be continued with --resume.
                spent = usage_cost_usd(usage_summary(usage))
                if spent + spent / done > max_cost_usd:
                    raise BudgetExceeded(
                        f"Stopping before {day}: ${spent:.2f} spent over {done} days, "
                        f"next day would likely pass the ${max_cost_usd:.2f} cap."
                    )
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
                    result = await graph.ainvoke(
                        {"symbol": symbol, "as_of": as_of},
                        config={"callbacks": [usage]},
                    )

                    execution_price = None
                    if execution_prices is not None:
                        execution_price = await execution_prices.open_on(symbol, day)
                        if execution_price is None:
                            # Loud, not silent: a real session with no bar
                            # means the price data is wrong, and a skipped
                            # fill would quietly change the result.
                            raise ValueError(
                                f"No opening price for {symbol} on {day}; "
                                "cannot fill this day's decision."
                            )

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
                                 technicals_snapshot, sentiment_snapshot,
                                 execution_price)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
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
                                execution_price,
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

                    await record_var_forecast(
                        conn,
                        decision_id=decision_id,
                        backtest_id=backtest_id,
                        symbol=symbol,
                        as_of=as_of,
                        risk_raw_output=risk_opinion.raw_output,
                        final_action=final_action,
                        final_quantity=final_quantity,
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
                        decision_price = (
                            execution_price
                            if execution_price is not None
                            else Decimal(str(raw_price))
                        )
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

        await _finish(conn, backtest_id, "SUCCESS", usage)

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
