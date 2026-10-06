"""
Reasoning trace (app/agent/trace.py, trace_audit.py; phase 10).

Unit tests run the real graph with fake chat models that ARE LangChain
chat models, so the trace captures LLM calls exactly as in production.
DB tests (skip without Postgres) run a backtest end to end and check the
trace is stored with the decision and that the pre-registered
completeness check holds.
"""

import os
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from app.agent.backtest import run_backtest
from app.agent.data_sources import HistoricalHeadlineSource
from app.agent.explainer import HeadlineRetriever, SYSTEM_PROMPT as EXPLAINER_PROMPT
from app.agent.graph import build_decision_graph
from app.agent.portfolio_manager import SYSTEM_PROMPT as PM_PROMPT
from app.agent.sentiment_analyst import SentimentScore
from app.agent.state import TentativeDecision
from app.agent.trace import TraceRecorder, record_trace
from app.agent.trace_audit import completeness, format_audit, load_steps, rebuild_explanation_check
from app.repository.backtest import BacktestRepository
from tests.agent_fakes import FakeHeadlineSource, FakeRepository, fixed_json_model, grounded_explainer_model
from tests.test_risk_manager import HIGH_VOL, SymbolPriceSource, _bars

AS_OF = datetime(2023, 3, 15, 12, tzinfo=timezone.utc)
BUY = TentativeDecision(action="BUY", quantity=10, confidence=0.8, reasoning="buy it")
NEUTRAL = SentimentScore(score=0.0, confidence=0.5, reasoning="t")


class RecordingRetriever:
    """Explainer retriever with no database: one fixed headline per day."""

    method = "fake"

    async def around(self, symbol, day, as_of, limit=8):
        from app.agent import trace
        from app.agent.explainer import RetrievedHeadline

        h = RetrievedHeadline(id=int(day.strftime("%m%d")), published_at=AS_OF, headline=f"news on {day}")
        trace.record(node="explainer", kind="retrieval",
                     input={"day": str(day), "as_of": as_of.isoformat(), "method": "fake"},
                     output={"results": [{"rank": 1, "id": h.id, "published_at": h.published_at.isoformat(),
                                          "headline": h.headline, "score": None}]})
        return [h]


async def _traced_run(**graph_kwargs):
    graph = build_decision_graph(
        price_source=SymbolPriceSource({"AAPL": _bars("AAPL", HIGH_VOL)}),
        headline_source=FakeHeadlineSource([]),
        repository=FakeRepository(),
        sentiment_llm=fixed_json_model(NEUTRAL),
        portfolio_llm=fixed_json_model(BUY, "fake-pm"),
        **graph_kwargs,
    )
    recorder = TraceRecorder()
    token = recorder.activate()
    try:
        state = await graph.ainvoke({"symbol": "AAPL", "as_of": AS_OF}, config={"callbacks": [recorder]})
    finally:
        TraceRecorder.deactivate(token)
    return state, recorder.steps


async def test_trace_covers_llm_calls_rules_retrieval_and_check():
    state, steps = await _traced_run(
        explainer_retriever=RecordingRetriever(), explainer_llm=grounded_explainer_model()
    )
    kinds = [(s["node"], s["kind"]) for s in steps]
    # No headlines -> the sentiment node makes no LLM call, so none is traced.
    assert kinds == [
        ("portfolio_manager", "llm"),
        ("risk_manager", "rule"),
        ("explainer", "retrieval"), ("explainer", "retrieval"), ("explainer", "retrieval"),
        ("explainer", "retrieval"), ("explainer", "retrieval"),
        ("explainer", "llm"),
        ("explainer", "check"),
    ]
    pm = steps[0]
    assert pm["input"]["messages"][0] == {"role": "system", "content": PM_PROMPT}
    assert '"action":"BUY"' in pm["output"]["content"]
    assert pm["model"] == "fake-pm" and pm["input_tokens"] > 0 and pm["duration_ms"] >= 0
    rule = steps[1]
    assert rule["output"]["verdict"] == "SCALE" and rule["output"]["var_max_qty"] == 5
    assert steps[7]["input"]["messages"][0]["content"] == EXPLAINER_PROMPT
    assert steps[8]["output"]["citations_valid"] is True


async def test_explanation_check_rebuilds_from_trace_alone():
    state, steps = await _traced_run(
        explainer_retriever=RecordingRetriever(), explainer_llm=grounded_explainer_model()
    )
    e = state["risk_explanation"]
    rebuilt = rebuild_explanation_check(steps)
    assert rebuilt == {
        "retrieved": e.retrieved, "cited_headline_ids": e.cited_headline_ids,
        "validation_problems": e.validation_problems, "citations_valid": e.citations_valid,
    }


async def test_llm_error_is_traced():
    from tests.agent_fakes import JsonFakeChatModel

    def boom(messages):
        raise RuntimeError("model down")

    graph = build_decision_graph(
        price_source=SymbolPriceSource({"AAPL": _bars("AAPL", HIGH_VOL)}),
        headline_source=FakeHeadlineSource([]), repository=FakeRepository(),
        sentiment_llm=fixed_json_model(NEUTRAL),
        portfolio_llm=JsonFakeChatModel(respond=boom),
    )
    recorder = TraceRecorder()
    token = recorder.activate()
    try:
        with pytest.raises(RuntimeError, match="model down"):
            await graph.ainvoke({"symbol": "AAPL", "as_of": AS_OF}, config={"callbacks": [recorder]})
    finally:
        TraceRecorder.deactivate(token)
    assert recorder.steps[0]["node"] == "portfolio_manager"
    assert "model down" in recorder.steps[0]["error"]


def test_credentials_never_reach_the_trace():
    recorder = TraceRecorder()
    recorder.on_chat_model_start(
        {}, [[]], run_id=uuid4(),
        invocation_params={"model": "claude-haiku-4-5", "anthropic_api_key": "sk-ant-abcdefghijklmnopqrstuvwxyz",
                           "default_headers": {"x-api-key": "secret"}, "max_tokens": 10},
    )
    params = recorder.steps[0]["input"]["params"]
    assert params == {"model": "claude-haiku-4-5", "max_tokens": 10}


async def test_record_trace_refuses_key_like_content():
    recorder = TraceRecorder()
    recorder.add(node="x", kind="check", input={"note": "leaked sk-ant-abcdefghijklmnopqrstuvwxyz0123"}, output={})
    with pytest.raises(ValueError, match="key-like"):
        await record_trace(None, decision_id=1, recorder=recorder)


def test_no_recorder_means_no_op():
    from app.agent import trace

    assert trace.record(node="x", kind="check", input={}, output={}) is None


# --------------------------------------------------------------------------
# End to end against Postgres: a backtest stores the trace with each
# decision, the audit reads it back, and completeness holds.
# --------------------------------------------------------------------------

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)
SYMBOL = "TRACETEST"
NAME = "test trace"


@pytest.fixture
async def pool():
    try:
        p = AsyncConnectionPool(DATABASE_URL, open=False)
        await p.open(wait=True, timeout=3)
    except psycopg.OperationalError:
        pytest.skip("no reachable Postgres database — skipping DB integration test")
        return
    async with p.connection() as conn:
        await conn.execute("INSERT INTO watchlist (symbol, active) VALUES (%s, false) ON CONFLICT DO NOTHING", (SYMBOL,))
        await conn.execute("DELETE FROM historical_headlines WHERE symbol = %s", (SYMBOL,))
        # Date-only stamps, like FNSPID, on a few of the bars' days.
        for d, text in ((date(2023, 1, 20), "TRACETEST earnings fall"), (date(2023, 1, 22), "TRACETEST plunges"),
                        (date(2023, 9, 11), "TRACETEST news yesterday")):
            await conn.execute("INSERT INTO historical_headlines (symbol, published_at, headline) VALUES (%s, %s, %s)",
                               (SYMBOL, datetime(d.year, d.month, d.day, tzinfo=timezone.utc), text))
    yield p
    async with p.connection() as conn:
        await conn.execute("DELETE FROM decisions WHERE run_id IN (SELECT id FROM runs WHERE backtest_id IN "
                           "(SELECT id FROM backtests WHERE name = %s))", (NAME,))
        await conn.execute("DELETE FROM runs WHERE backtest_id IN (SELECT id FROM backtests WHERE name = %s)", (NAME,))
        await conn.execute("DELETE FROM backtests WHERE name = %s", (NAME,))
        await conn.execute("DELETE FROM historical_headlines WHERE symbol = %s", (SYMBOL,))
    await p.close()


async def test_backtest_stores_trace_with_decision_and_it_audits(pool):
    bars = _bars(SYMBOL, HIGH_VOL)  # VaR 8%: every BUY is cut, so the explainer fires

    class Prices:
        async def get_recent_bars(self, symbol, as_of, lookback_days=30):
            return [b for b in bars if b.timestamp < as_of]

    def graph(backtest_id):
        return build_decision_graph(
            price_source=Prices(), headline_source=HistoricalHeadlineSource(pool),
            repository=BacktestRepository(pool, backtest_id),
            sentiment_llm=fixed_json_model(NEUTRAL), portfolio_llm=fixed_json_model(BUY, "fake-pm"),
            explainer_retriever=HeadlineRetriever(pool, "fts"), explainer_llm=grounded_explainer_model(),
        )

    day = date(2023, 9, 12)  # after all 251 bars: a full VaR window
    backtest_id = await run_backtest(pool, graph, name=NAME, symbols=[SYMBOL],
                                     window_start=day, window_end=day, trading_days=[day])
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT d.id, d.symbol, d.action, d.reasoning, d.run_id, r.as_of FROM decisions d "
            "JOIN runs r ON r.id = d.run_id WHERE r.backtest_id = %s", (backtest_id,))
        row = await cur.fetchone()
        decision = dict(zip(("id", "symbol", "action", "reasoning", "run_id", "as_of"), row))
        steps = await load_steps(conn, decision["id"])
        checked, mismatches = await completeness(conn, [decision["id"]])

    nodes = [(s["node"], s["kind"]) for s in steps]
    assert ("sentiment_analyst", "retrieval") in nodes  # headline lookup traced
    assert ("sentiment_analyst", "llm") in nodes        # 1 headline available -> LLM called
    assert ("explainer", "check") in nodes
    sentiment_retrieval = next(s for s in steps if s["node"] == "sentiment_analyst" and s["kind"] == "retrieval")
    # The decision at 2023-09-12 12:00 UTC sees the headline dated 09-11.
    assert [r["headline"] for r in sentiment_retrieval["output"]["results"]] == ["TRACETEST news yesterday"]
    explainer_hits = [r["headline"] for s in steps if s["node"] == "explainer" and s["kind"] == "retrieval"
                      for r in s["output"]["results"]]
    assert "TRACETEST plunges" in explainer_hits  # dated 2023-01-22, the worst-loss day
    assert checked == 1 and mismatches == []
    report = format_audit(decision, steps)
    assert f"Decision {decision['id']}: {SYMBOL}" in report and "explainer · check" in report
