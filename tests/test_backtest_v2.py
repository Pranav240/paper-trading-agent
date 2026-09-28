"""
Backtest runner changes made before the V1 reruns (V2, phase 07):
trading days from a real calendar, fills at the decision day's open,
FAILED on a crash, and LLM token usage saved per run.

Real Postgres, skipping if none is reachable (same pattern as
test_backtest.py). The graph here is a tiny stand-in that returns the
state keys run_backtest reads, so each test controls exactly what the
"agents" say and which chat model gets called.
"""

import os
from datetime import date, datetime, timezone
from decimal import Decimal

import psycopg
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from psycopg_pool import AsyncConnectionPool

from app.agent.backtest import DEFAULT_SLIPPAGE_BPS, run_backtest
from app.agent.state import AgentOpinion, GraphState, RiskVerdict, TentativeDecision

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)
SYMBOL = "V2TEST"
NAME = "test backtest v2"


@pytest.fixture
async def pool():
    try:
        test_pool = AsyncConnectionPool(DATABASE_URL, open=False)
        await test_pool.open(wait=True, timeout=3)
    except psycopg.OperationalError:
        pytest.skip("no reachable Postgres database — skipping DB integration test")
        return
    async with test_pool.connection() as conn:
        await conn.execute(
            "INSERT INTO watchlist (symbol, active) VALUES (%s, false) "
            "ON CONFLICT (symbol) DO NOTHING", (SYMBOL,)
        )
    yield test_pool
    async with test_pool.connection() as conn:
        await conn.execute(
            "DELETE FROM decisions WHERE run_id IN (SELECT id FROM runs WHERE backtest_id IN "
            "(SELECT id FROM backtests WHERE name = %s))", (NAME,)
        )
        await conn.execute(
            "DELETE FROM runs WHERE backtest_id IN (SELECT id FROM backtests WHERE name = %s)",
            (NAME,),
        )
        await conn.execute("DELETE FROM backtests WHERE name = %s", (NAME,))
    await test_pool.close()


def _opinion(agent: str, raw: dict | None = None) -> AgentOpinion:
    return AgentOpinion(agent_name=agent, opinion="X", reasoning="t", raw_output=raw or {})


def _graph(action: str = "BUY", quantity: int = 5, chat_model=None, fail: bool = False):
    """Stand-in decision graph. If `chat_model` is given, the node calls it
    once per day, like a real LLM node would."""

    async def node(state: GraphState) -> dict:
        if fail:
            raise RuntimeError("simulated API failure")
        if chat_model is not None:
            await chat_model.ainvoke("hello")
        return {
            "technical_opinion": _opinion("technical_analyst", {"current_price": 100.0}),
            "sentiment_opinion": _opinion("sentiment_analyst"),
            "portfolio_opinion": _opinion("portfolio_manager"),
            "risk_opinion": _opinion("risk_manager"),
            "tentative_decision": TentativeDecision(
                action=action, quantity=quantity, confidence=0.5, reasoning="t"
            ),
            "risk_verdict": RiskVerdict(opinion="APPROVE", reasoning="t"),
            "final_action": action,
            "final_quantity": quantity,
        }

    graph = StateGraph(GraphState)
    graph.add_node("all", node)
    graph.add_edge(START, "all")
    graph.add_edge("all", END)
    return graph.compile()


class Opens:
    def __init__(self, opens: dict):
        self._opens = opens

    async def open_on(self, symbol, day):
        return self._opens.get(day)


def _usage_model(n_calls: int) -> GenericFakeChatModel:
    return GenericFakeChatModel(
        messages=iter(
            AIMessage(
                content="ok",
                usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
                response_metadata={"model_name": "fake-model"},
            )
            for _ in range(n_calls)
        )
    )


async def _backtest_row(pool, backtest_id):
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT status, llm_usage, config FROM backtests WHERE id = %s", (backtest_id,)
        )
        return await cur.fetchone()


async def test_calendar_open_fills_config_and_usage(pool):
    # Jan 11 2022 left out, as a holiday would be.
    days = [date(2022, 1, 10), date(2022, 1, 12)]
    opens = {days[0]: Decimal("150.00"), days[1]: Decimal("160.00")}

    backtest_id = await run_backtest(
        pool,
        lambda _id: _graph("BUY", 5, chat_model=_usage_model(len(days))),
        name=NAME, symbols=[SYMBOL],
        window_start=days[0], window_end=days[-1],
        config={"git_commit": "abc"},
        trading_days=days,
        execution_prices=Opens(opens),
    )

    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT r.as_of::date, d.execution_price FROM decisions d "
            "JOIN runs r ON d.run_id = r.id WHERE r.backtest_id = %s ORDER BY r.as_of",
            (backtest_id,),
        )
        decisions = await cur.fetchall()
        cur = await conn.execute(
            "SELECT entry_price FROM backtest_outcomes WHERE backtest_id = %s ORDER BY opened_at",
            (backtest_id,),
        )
        fills = [row[0] for row in await cur.fetchall()]

    assert decisions == [(days[0], Decimal("150.0000")), (days[1], Decimal("160.0000"))]
    slip = 1 + DEFAULT_SLIPPAGE_BPS / Decimal(10000)
    # Filled at each day's open plus slippage, not at current_price (100).
    assert fills == [
        (Decimal("150") * slip).quantize(Decimal("0.0001")),
        (Decimal("160") * slip).quantize(Decimal("0.0001")),
    ]

    status, usage, config = await _backtest_row(pool, backtest_id)
    assert status == "SUCCESS"
    assert config == {"git_commit": "abc"}
    # Two days, one call each, carried through LangGraph by the callback.
    assert usage == {
        "fake-model": {"input_tokens": 200, "output_tokens": 20, "total_tokens": 220}
    }


async def test_crash_marks_backtest_failed(pool):
    with pytest.raises(RuntimeError, match="simulated API failure"):
        await run_backtest(
            pool, lambda _id: _graph(fail=True),
            name=NAME, symbols=[SYMBOL],
            window_start=date(2022, 1, 10), window_end=date(2022, 1, 12),
        )
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT status, llm_usage FROM backtests WHERE name = %s", (NAME,)
        )
        assert await cur.fetchall() == [("FAILED", {})]


async def test_missing_open_fails_loudly(pool):
    with pytest.raises(ValueError, match="No opening price"):
        await run_backtest(
            pool, lambda _id: _graph(),
            name=NAME, symbols=[SYMBOL],
            window_start=date(2022, 1, 10), window_end=date(2022, 1, 10),
            trading_days=[date(2022, 1, 10)],
            execution_prices=Opens({}),
        )
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT status FROM backtests WHERE name = %s", (NAME,))
        assert await cur.fetchall() == [("FAILED",)]
