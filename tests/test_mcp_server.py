"""
The read-only MCP server (app/mcp_server.py, plan in docs/mcp-plan.md).

Without Postgres: the tool list and its read-only annotations, input
validation, limit capping, and the error a client sees when the database
is down. With Postgres (skipped without it, like the other DB tests): a
fake-model backtest with one VaR-cut decision -- explanation and trace
included -- read back through an in-process MCP client, plus proof that
the server's connection cannot write.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

import psycopg
import pytest
from mcp import Client
from psycopg_pool import AsyncConnectionPool

from app import mcp_server
from app.agent.backtest import run_backtest
from app.agent.data_sources import HistoricalHeadlineSource
from app.agent.explainer import HeadlineRetriever
from app.agent.graph import build_decision_graph
from app.agent.sentiment_analyst import SentimentScore
from app.agent.state import TentativeDecision
from app.agent.trace_audit import completeness, format_audit, load_steps
from app.repository.backtest import BacktestRepository
from tests.agent_fakes import fixed_json_model, grounded_explainer_model
from tests.test_risk_manager import HIGH_VOL, _bars

TOOLS = {"list_backtests", "list_decisions", "get_decision", "explain_decision", "get_trace"}


async def call(name: str, args: dict | None = None):
    async with Client(mcp_server.server) as client:
        return await client.call_tool(name, args or {})


# --------------------------------------------------------------------------
# No database needed
# --------------------------------------------------------------------------

async def test_exactly_the_planned_tools_all_read_only():
    async with Client(mcp_server.server) as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == TOOLS
    for t in tools:
        assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False, t.name


def test_limits_are_capped():
    assert mcp_server._limit(10_000) == mcp_server.MAX_LIMIT == 200
    assert mcp_server._limit(0) == 1


async def test_unknown_action_is_a_plain_error():
    result = await call("list_decisions", {"action": "SHORT"})
    assert result.is_error and "action must be one of BUY, SELL, HOLD" in result.content[0].text


async def test_database_down_is_a_plain_error(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://pta:x@127.0.0.1:1/none")
    result = await call("list_backtests")
    assert result.is_error and "database is not reachable" in result.content[0].text


# --------------------------------------------------------------------------
# Against Postgres
# --------------------------------------------------------------------------

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)
SYMBOL = "MCPTEST"
NAME = "test mcp server"
BUY = TentativeDecision(action="BUY", quantity=10, confidence=0.8, reasoning="buy it")
NEUTRAL = SentimentScore(score=0.0, confidence=0.5, reasoning="t")


async def _cleanup(p):
    async with p.connection() as conn:
        await conn.execute("DELETE FROM decisions WHERE run_id IN (SELECT id FROM runs WHERE backtest_id IN "
                           "(SELECT id FROM backtests WHERE name = %s))", (NAME,))
        await conn.execute("DELETE FROM runs WHERE backtest_id IN (SELECT id FROM backtests WHERE name = %s)", (NAME,))
        await conn.execute("DELETE FROM backtests WHERE name = %s", (NAME,))
        await conn.execute("DELETE FROM historical_headlines WHERE symbol = %s", (SYMBOL,))


@pytest.fixture
async def recorded():
    """One backtest day on which the VaR budget cuts a BUY: decision,
    opinions, VaR forecast, explanation and trace all stored."""
    try:
        p = AsyncConnectionPool(DATABASE_URL, open=False)
        await p.open(wait=True, timeout=3)
    except psycopg.OperationalError:
        pytest.skip("no reachable Postgres database — skipping DB integration test")
        return
    await _cleanup(p)
    async with p.connection() as conn:
        await conn.execute("INSERT INTO watchlist (symbol, active) VALUES (%s, false) ON CONFLICT DO NOTHING", (SYMBOL,))
        for d, text in ((date(2023, 1, 20), "MCPTEST earnings fall"), (date(2023, 1, 22), "MCPTEST plunges"),
                        (date(2023, 9, 11), "MCPTEST news yesterday")):
            await conn.execute("INSERT INTO historical_headlines (symbol, published_at, headline) VALUES (%s, %s, %s)",
                               (SYMBOL, datetime(d.year, d.month, d.day, tzinfo=timezone.utc), text))
    bars = _bars(SYMBOL, HIGH_VOL)  # VaR ~8%: every BUY is cut, so the explainer fires

    class Prices:
        async def get_recent_bars(self, symbol, as_of, lookback_days=30):
            return [b for b in bars if b.timestamp < as_of]

    def graph(backtest_id):
        return build_decision_graph(
            price_source=Prices(), headline_source=HistoricalHeadlineSource(p),
            repository=BacktestRepository(p, backtest_id),
            sentiment_llm=fixed_json_model(NEUTRAL), portfolio_llm=fixed_json_model(BUY, "fake-pm"),
            explainer_retriever=HeadlineRetriever(p, "fts"), explainer_llm=grounded_explainer_model(),
        )

    day = date(2023, 9, 12)
    backtest_id = await run_backtest(p, graph, name=NAME, symbols=[SYMBOL],
                                     window_start=day, window_end=day, trading_days=[day])
    async with p.connection() as conn:
        cur = await conn.execute("SELECT d.id FROM decisions d JOIN runs r ON r.id = d.run_id "
                                 "WHERE r.backtest_id = %s", (backtest_id,))
        (decision_id,) = await cur.fetchone()
    yield {"pool": p, "backtest_id": backtest_id, "decision_id": decision_id}
    await _cleanup(p)
    await p.close()


async def test_the_server_connection_cannot_write(recorded):
    async with await mcp_server._connect() as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            await conn.execute("DELETE FROM decisions WHERE id = %s", (recorded["decision_id"],))
    async with recorded["pool"].connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM decisions WHERE id = %s", (recorded["decision_id"],))
        assert (await cur.fetchone())[0] == 1


async def test_list_backtests_and_decisions(recorded):
    backtests = (await call("list_backtests", {"limit": 200})).structured_content["backtests"]
    mine = next(b for b in backtests if b["id"] == recorded["backtest_id"])
    assert mine["name"] == NAME and mine["status"] == "SUCCESS" and mine["decisions"] == 1

    rows = (await call("list_decisions", {"backtest_id": recorded["backtest_id"],
                                          "var_changed_only": True})).structured_content["decisions"]
    assert [r["id"] for r in rows] == [recorded["decision_id"]]
    assert rows[0]["var_changed"] and rows[0]["has_explanation"] and rows[0]["has_trace"]
    none = (await call("list_decisions", {"backtest_id": recorded["backtest_id"], "action": "sell"}))
    assert none.structured_content["count"] == 0


async def test_get_decision_has_opinions_and_var(recorded):
    out = (await call("get_decision", {"decision_id": recorded["decision_id"]})).structured_content
    assert out["decision"]["symbol"] == SYMBOL and out["decision"]["backtest_id"] == recorded["backtest_id"]
    agents = {o["agent_name"] for o in out["agent_opinions"]}
    assert {"technical_analyst", "sentiment_analyst", "portfolio_manager", "risk_manager"} <= agents
    var = out["var_forecast"]
    assert var["var_budget"] == 0.02 and var["var_max_qty"] < 20 and var["risk_flags"]


async def test_explain_decision_returns_cited_headlines(recorded):
    out = (await call("explain_decision", {"decision_id": recorded["decision_id"]})).structured_content
    assert out["summary"] and out["citations_valid"] is True
    assert {h["id"] for h in out["cited_headlines"]} == set(out["cited_headline_ids"])
    assert "MCPTEST plunges" in {h["headline"] for h in out["cited_headlines"]}


async def test_get_trace_matches_the_audit_script(recorded):
    """No second, drifting implementation: the tool's text is exactly what
    scripts/audit_decision.py prints for the same decision."""
    text = (await call("get_trace", {"decision_id": recorded["decision_id"]})).content[0].text
    async with recorded["pool"].connection() as conn:
        cur = await conn.execute("SELECT d.id, d.symbol, d.action, d.reasoning, d.run_id, r.as_of "
                                 "FROM decisions d JOIN runs r ON r.id = d.run_id WHERE d.id = %s",
                                 (recorded["decision_id"],))
        decision = dict(zip(("id", "symbol", "action", "reasoning", "run_id", "as_of"), await cur.fetchone()))
        steps = await load_steps(conn, decision["id"])
        checked, mismatches = await completeness(conn, [decision["id"]])
    assert checked == 1 and mismatches == []
    assert text == format_audit(decision, steps) + "\n\ncompleteness: explanation rebuilt from the trace matches the stored row"


async def test_missing_rows_are_plain_errors(recorded):
    result = await call("get_decision", {"decision_id": 10**12})
    assert result.is_error and "There is no decision" in result.content[0].text
    # A decision with neither explanation nor trace, like every pre-phase-10 row.
    async with recorded["pool"].connection() as conn:
        cur = await conn.execute(
            "INSERT INTO runs (mode, backtest_id, as_of, status) VALUES ('BACKTEST', %s, %s, 'SUCCESS') RETURNING id",
            (recorded["backtest_id"], datetime(2023, 9, 13, 12, tzinfo=timezone.utc)))
        (run_id,) = await cur.fetchone()
        cur = await conn.execute(
            "INSERT INTO decisions (run_id, symbol, action, confidence, reasoning) "
            "VALUES (%s, %s, 'HOLD', 0.5, 'plain') RETURNING id", (run_id, SYMBOL))
        (plain_id,) = await cur.fetchone()
    assert "has no explanation" in (await call("explain_decision", {"decision_id": plain_id})).content[0].text
    assert "has no trace" in (await call("get_trace", {"decision_id": plain_id})).content[0].text
