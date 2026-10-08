"""
Read-only MCP server over the stored decision record (add-on; plan in
docs/mcp-plan.md).

An MCP client (Claude Desktop, Claude Code) starts this as a local
process and talks to it over stdio; no port is opened. Five tools answer
questions about recorded backtests and decisions from the database
itself: nothing is computed here that the project has not already
computed and stored, so the server cannot produce a number the record
does not contain.

Read-only by construction: every connection is opened with
default_transaction_read_only=on, so a write fails in Postgres even if a
tool had a bug. No tool triggers a run -- a run spends API money, and an
LLM client should not be able to start one.

Run (the client normally does this, see README):
    .\\papertrading\\Scripts\\python.exe -m app.mcp_server
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from psycopg.rows import dict_row

from app.agent.explainer import TRIGGER_FLAGS
from app.agent.trace_audit import completeness, format_audit, load_steps

load_dotenv()

DEFAULT_LIMIT = 20
MAX_LIMIT = 200
ACTIONS = ("BUY", "SELL", "HOLD")

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

server = MCPServer(
    name="paper-trading-agent",
    instructions=(
        "Read-only access to the Paper Trading Agent's recorded backtests and decisions "
        "(paper trading only, never real money). Answer from what the tools return; if a "
        "tool does not return something, say it is not recorded rather than inferring it. "
        "Typical path: list_backtests -> list_decisions -> get_decision -> "
        "explain_decision / get_trace."
    ),
)


def _database_url() -> str:
    # Read at call time, not import time, so tests and clients can point it elsewhere.
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
    )


async def _connect() -> psycopg.AsyncConnection:
    try:
        return await psycopg.AsyncConnection.connect(
            _database_url(), options="-c default_transaction_read_only=on", connect_timeout=5
        )
    except psycopg.OperationalError:
        raise ToolError("The project database is not reachable (is the pta-postgres container running?).")


def _clean(value: Any) -> Any:
    """JSON-safe values for the client: Decimals to floats, dates to ISO strings."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


async def _fetch(sql: str, params: tuple = ()) -> list[dict]:
    async with await _connect() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            return [_clean(r) for r in await cur.fetchall()]


async def _decision_row(conn, decision_id: int) -> dict:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT d.id, d.symbol, d.action, d.confidence, d.reasoning, d.run_id, "
            "d.execution_price, r.as_of, r.mode, r.backtest_id "
            "FROM decisions d JOIN runs r ON r.id = d.run_id WHERE d.id = %s",
            (decision_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise ToolError(f"There is no decision {decision_id}.")
    return row


@server.tool(annotations=READ_ONLY)
async def list_backtests(limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """List recorded backtests, newest first: window, status, models, git
    commit and how many decisions each made. Paper trading only."""
    rows = await _fetch(
        "SELECT b.id, b.name, b.window_start, b.window_end, b.status, "
        "b.config->'models' AS models, b.config->>'sentiment_mode' AS sentiment_mode, "
        "b.config->>'git_commit' AS git_commit, "
        "(SELECT count(*) FROM decisions d JOIN runs r ON r.id = d.run_id "
        " WHERE r.backtest_id = b.id) AS decisions "
        "FROM backtests b ORDER BY b.id DESC LIMIT %s",
        (_limit(limit),),
    )
    return {"backtests": rows, "count": len(rows)}


@server.tool(annotations=READ_ONLY)
async def list_decisions(
    backtest_id: int | None = None,
    symbol: str | None = None,
    action: str | None = None,
    var_changed_only: bool = False,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """List recorded decisions, oldest first within the filters. Without
    backtest_id, only live (non-backtest) decisions are listed.
    var_changed_only keeps decisions where the VaR budget cut or blocked
    the trade -- the ones that have explanations."""
    if action is not None and action.upper() not in ACTIONS:
        raise ToolError(f"action must be one of {', '.join(ACTIONS)}.")
    where = ["r.backtest_id = %s" if backtest_id is not None else "r.mode = 'LIVE'"]
    params: list[Any] = [backtest_id] if backtest_id is not None else []
    if symbol:
        where.append("d.symbol = %s")
        params.append(symbol.upper())
    if action:
        where.append("d.action = %s")
        params.append(action.upper())
    if var_changed_only:
        where.append("v.risk_flags && %s")
        params.append(list(TRIGGER_FLAGS))
    params.append(_limit(limit))
    rows = await _fetch(
        "SELECT d.id, r.as_of, d.symbol, d.action, d.confidence, r.backtest_id, "
        "COALESCE(v.risk_flags && %s, false) AS var_changed, "
        "EXISTS (SELECT 1 FROM risk_explanations e WHERE e.decision_id = d.id) AS has_explanation, "
        "EXISTS (SELECT 1 FROM trace_steps t WHERE t.decision_id = d.id) AS has_trace "
        "FROM decisions d JOIN runs r ON r.id = d.run_id "
        "LEFT JOIN var_forecasts v ON v.decision_id = d.id "
        f"WHERE {' AND '.join(where)} ORDER BY r.as_of, d.id LIMIT %s",
        (list(TRIGGER_FLAGS), *params),
    )
    return {"decisions": rows, "count": len(rows)}


@server.tool(annotations=READ_ONLY)
async def get_decision(decision_id: int) -> dict[str, Any]:
    """One decision in full: the final action, every agent's opinion and
    reasoning (technical, sentiment, portfolio manager, risk manager), and
    the VaR forecast with the quantity it allowed."""
    async with await _connect() as conn:
        decision = await _decision_row(conn, decision_id)
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT agent_name, opinion, confidence, reasoning FROM agent_opinions "
                "WHERE decision_id = %s ORDER BY id",
                (decision_id,),
            )
            opinions = await cur.fetchall()
            await cur.execute(
                "SELECT confidence, window_size, var, cvar, parametric_var, var_budget, "
                "var_max_qty, position_qty, last_close, risk_flags, realized_return, breach "
                "FROM var_forecasts WHERE decision_id = %s",
                (decision_id,),
            )
            var = await cur.fetchone()
    return _clean({"decision": decision, "agent_opinions": opinions, "var_forecast": var})


@server.tool(annotations=READ_ONLY)
async def explain_decision(decision_id: int) -> dict[str, Any]:
    """The stored explanation of why the risk engine cut or blocked a
    trade: summary, the loss days it is built on, the headlines it cited,
    whether those citations passed the grounding check, and the no-news
    template baseline. Only VaR-changed decisions have one."""
    async with await _connect() as conn:
        await _decision_row(conn, decision_id)
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT summary, drivers, cited_headline_ids, citations_valid, validation_problems, "
                "trigger_flags, model, prompt_version, retrieval_method, template_baseline "
                "FROM risk_explanations WHERE decision_id = %s",
                (decision_id,),
            )
            row = await cur.fetchone()
            if row is None:
                raise ToolError(
                    f"Decision {decision_id} has no explanation: only decisions where the VaR "
                    "budget cut or blocked the trade are explained."
                )
            cited = []
            if row["cited_headline_ids"]:
                await cur.execute(
                    "SELECT id, published_at::date AS date, headline FROM historical_headlines "
                    "WHERE id = ANY(%s) ORDER BY published_at, id",
                    (row["cited_headline_ids"],),
                )
                cited = await cur.fetchall()
    return _clean({**row, "cited_headlines": cited})


# Plain text only: a structured copy would send the whole trace twice.
@server.tool(annotations=READ_ONLY, structured_output=False)
async def get_trace(decision_id: int, full: bool = False) -> str:
    """The decision's reasoning trace, step by step: prompts, model replies,
    headline lookups, rules and checks, as stored when it was made. Long
    text is shortened unless full=True. Ends with the completeness check
    when the decision has an explanation."""
    async with await _connect() as conn:
        decision = await _decision_row(conn, decision_id)
        steps = await load_steps(conn, decision_id)
        if not steps:
            raise ToolError(f"Decision {decision_id} has no trace (it was made before tracing existed).")
        report = format_audit(decision, steps, full=full)
        checked, mismatches = await completeness(conn, [decision_id])
    if checked:
        report += "\n\ncompleteness: " + (
            "explanation rebuilt from the trace matches the stored row"
            if not mismatches else "MISMATCH\n  " + "\n  ".join(mismatches)
        )
    return report


def main() -> None:
    if sys.platform == "win32":
        # psycopg's async driver refuses Windows' default Proactor loop (see tests/conftest.py).
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    server.run()


if __name__ == "__main__":
    main()
