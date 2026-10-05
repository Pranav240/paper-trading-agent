"""
Explainer node (app/agent/explainer.py, phase 08), with fake LLMs.

Unit tests cover the trigger, the grounding check and the no-news
template. The retrieval tests use real Postgres (skipping if none is
reachable, like test_backtest.py): the ranking and the look-ahead bound
are SQL, which a fake can't check.
"""

import os
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from app.agent.explainer import (
    DRIVER_DAYS,
    ExplanationCall,
    DriverExplanation,
    HeadlineRetriever,
    RetrievedHeadline,
    SYSTEM_PROMPT,
    make_explainer_node,
    template_explanation,
    triggered,
    validate,
)
from app.agent.risk_math import tail_losses
from app.agent.state import AgentOpinion, RiskVerdict, TentativeDecision

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent",
)

RAW = {
    "risk_flags": ["var_budget_veto", "over_var_budget"],
    "var_95": 0.031,
    "var_budget": 0.02,
    "var_max_qty": 12,
    "current_qty": 12,
    "var_tail": [
        {"date": "2022-09-13", "return": -0.0579},
        {"date": "2022-05-18", "return": -0.0567},
        {"date": "2022-09-29", "return": -0.0479},
    ],
}


def test_tail_losses_are_the_var_days():
    # 250 returns; the 13 worst (k for 95%/250) are the 13 most negative.
    returns = [-0.001 * i for i in range(250)]
    labels = list(range(250))
    tail = tail_losses(returns, labels)
    assert [label for label, _ in tail] == list(range(249, 236, -1))
    assert tail[0][1] == pytest.approx(-0.249)
    assert tail_losses(returns[:100], labels[:100]) is None


def test_trigger_only_when_the_budget_changed_the_trade():
    assert triggered(RAW) == ["var_budget_veto"]
    assert triggered({"risk_flags": ["over_var_budget"]}) == []
    assert triggered({"risk_flags": ["high_correlation:MSFT"]}) == []
    assert triggered({}) == []


def test_template_baseline_states_the_facts_without_news():
    text = template_explanation(RAW, "VETO", 0)
    assert text.startswith("The risk engine blocked the buy: 95% one-day VaR is 3.10%")
    assert "at most 12 shares are allowed and 12 are already held" in text
    assert "2022-09-13 -5.8%" in text
    assert "cut the buy to 5 shares" in template_explanation(RAW, "SCALE", 5)


RETRIEVED = {"2022-09-13": [11, 12], "2022-05-18": [], "2022-09-29": [21]}


def _call(drivers):
    return ExplanationCall(summary="s", drivers=[DriverExplanation(**d) for d in drivers])


def test_validate_accepts_grounded_citations():
    call = _call([
        {"date": "2022-09-13", "cause": "CPI", "headline_ids": [12]},
        {"date": "2022-05-18", "cause": "no news on record", "headline_ids": []},
        {"date": "2022-09-29", "cause": "demand", "headline_ids": [21]},
    ])
    assert validate(call, RETRIEVED) == ([12, 21], [])


def test_validate_flags_citations_from_another_day_or_nowhere():
    call = _call([
        {"date": "2022-09-13", "cause": "x", "headline_ids": [21]},   # 21 belongs to 09-29
        {"date": "2022-05-18", "cause": "x", "headline_ids": [99]},   # never retrieved
        {"date": "2022-01-01", "cause": "x", "headline_ids": []},     # not a driver day
    ])
    cited, problems = validate(call, RETRIEVED)
    assert cited == [21, 99]
    assert problems == [
        "headline 21 was not retrieved for 2022-09-13",
        "headline 99 was not retrieved for 2022-05-18",
        "unknown driver date 2022-01-01",
        "no driver entry for 2022-09-29",
    ]


class FakeRetriever:
    method = "fake"

    def __init__(self, by_day):
        self.by_day, self.calls = by_day, []

    async def around(self, symbol, day, as_of, limit=8):
        self.calls.append((symbol, day, as_of))
        return self.by_day.get(day.isoformat(), [])


class RecordingLLM:
    def __init__(self, response):
        self.response, self.schema, self.messages = response, None, None

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    async def ainvoke(self, messages):
        self.messages = messages
        return self.response


def _headline(i, text):
    return RetrievedHeadline(id=i, published_at=datetime(2022, 9, 13, 14, tzinfo=timezone.utc), headline=text)


AS_OF = datetime(2023, 3, 15, 12, tzinfo=timezone.utc)


def _state(raw, opinion="VETO", final_qty=0):
    return {
        "symbol": "AAPL",
        "as_of": AS_OF,
        "tentative_decision": TentativeDecision(action="BUY", quantity=5, confidence=0.6, reasoning="t"),
        "risk_verdict": RiskVerdict(opinion=opinion, reasoning="t"),
        "risk_opinion": AgentOpinion(agent_name="risk_manager", opinion=opinion, reasoning="t", raw_output=raw),
        "final_action": "HOLD" if opinion == "VETO" else "BUY",
        "final_quantity": final_qty,
    }


async def test_node_skips_when_not_triggered():
    retriever = FakeRetriever({})
    llm = RecordingLLM(None)
    result = await make_explainer_node(retriever, llm=llm)(_state({**RAW, "risk_flags": []}))
    assert result == {"risk_explanation": None}
    assert retriever.calls == [] and llm.messages is None  # no retrieval, no LLM call


async def test_node_explains_from_retrieved_headlines_only():
    retriever = FakeRetriever({
        "2022-09-13": [_headline(11, "Apple slides as hot CPI print hits tech")],
        "2022-09-29": [_headline(21, "Apple drops after report it scraps iPhone output increase")],
    })
    llm = RecordingLLM(_call([
        {"date": "2022-09-13", "cause": "inflation data", "headline_ids": [11]},
        {"date": "2022-05-18", "cause": "no headline on record", "headline_ids": []},
        {"date": "2022-09-29", "cause": "iPhone demand", "headline_ids": [21]},
    ]))
    result = await make_explainer_node(retriever, llm=llm)(_state(RAW))
    e = result["risk_explanation"]

    assert llm.schema is ExplanationCall
    assert llm.messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    user = llm.messages[1]["content"]
    assert "[11] Apple slides as hot CPI print hits tech" in user
    assert "2022-05-18 (daily return -5.67%):\n  (no headlines on record)" in user
    # Every retrieval is bounded by the decision time.
    assert {c[2] for c in retriever.calls} == {AS_OF}
    assert len(retriever.calls) == min(DRIVER_DAYS, len(RAW["var_tail"]))

    assert e.citations_valid and e.validation_problems == []
    assert e.cited_headline_ids == [11, 21]
    assert e.retrieved == {"2022-09-13": [11], "2022-05-18": [], "2022-09-29": [21]}
    assert e.trigger_flags == ["var_budget_veto"]
    assert e.drivers[0]["return"] == pytest.approx(-0.0579)
    assert e.template_baseline.startswith("The risk engine blocked the buy")


async def test_node_keeps_but_flags_an_ungrounded_explanation():
    retriever = FakeRetriever({"2022-09-13": [_headline(11, "Apple slides")]})
    llm = RecordingLLM(_call([
        {"date": "2022-09-13", "cause": "x", "headline_ids": [11]},
        {"date": "2022-05-18", "cause": "invented", "headline_ids": [555]},
        {"date": "2022-09-29", "cause": "x", "headline_ids": []},
    ]))
    e = (await make_explainer_node(retriever, llm=llm)(_state(RAW)))["risk_explanation"]
    assert not e.citations_valid
    assert e.validation_problems == ["headline 555 was not retrieved for 2022-05-18"]
    assert e.summary == "s"  # stored as written, not repaired


# --------------------------------------------------------------------------
# Retrieval SQL, against real Postgres.
# --------------------------------------------------------------------------

SYMBOL = "EXPTEST"
NY_DAY = date(2022, 9, 13)


@pytest.fixture
async def pool():
    try:
        test_pool = AsyncConnectionPool(DATABASE_URL, open=False)
        await test_pool.open(wait=True, timeout=3)
    except psycopg.OperationalError:
        pytest.skip("no reachable Postgres database — skipping DB integration test")
        return
    utc = timezone.utc
    rows = [
        # (published_at UTC, headline)
        (datetime(2022, 9, 11, 23, tzinfo=utc), "too early: two days before"),          # 19:00 NY Sep 11
        (datetime(2022, 9, 12, 14, tzinfo=utc), "12 Stocks to Buy Now"),                 # listicle, Sep 12
        (datetime(2022, 9, 13, 13, tzinfo=utc), "Apple falls as hot CPI inflation data hits tech"),
        (datetime(2022, 9, 13, 18, tzinfo=utc), "3 Dividend Stocks for Retirees"),       # latest in window
        (datetime(2022, 9, 14, 3, tzinfo=utc), "Late night: still Sep 13 in New York"), # 23:00 NY Sep 13
        (datetime(2022, 9, 14, 5, tzinfo=utc), "too late: Sep 14 in New York"),        # 01:00 NY Sep 14
    ]
    async with test_pool.connection() as conn:
        await conn.execute("DELETE FROM historical_headlines WHERE symbol = %s", (SYMBOL,))
        for ts, text in rows:
            await conn.execute(
                "INSERT INTO historical_headlines (symbol, published_at, headline) VALUES (%s, %s, %s)",
                (SYMBOL, ts, text),
            )
    yield test_pool
    async with test_pool.connection() as conn:
        await conn.execute("DELETE FROM historical_headlines WHERE symbol = %s", (SYMBOL,))
    await test_pool.close()


LATER = datetime(2023, 1, 1, tzinfo=timezone.utc)


async def test_fts_ranks_relevant_headline_first(pool):
    got = await HeadlineRetriever(pool, "fts").around(SYMBOL, NY_DAY, LATER, limit=3)
    assert got[0].headline == "Apple falls as hot CPI inflation data hits tech"


async def test_recent_baseline_orders_by_time_only(pool):
    got = await HeadlineRetriever(pool, "recent").around(SYMBOL, NY_DAY, LATER, limit=3)
    assert [h.headline for h in got] == [
        "Late night: still Sep 13 in New York",
        "3 Dividend Stocks for Retirees",
        "Apple falls as hot CPI inflation data hits tech",
    ]


async def test_window_is_day_before_through_day_in_new_york(pool):
    got = await HeadlineRetriever(pool, "recent").around(SYMBOL, NY_DAY, LATER, limit=50)
    texts = {h.headline for h in got}
    assert "too early: two days before" not in texts
    assert "too late: Sep 14 in New York" not in texts
    assert len(texts) == 4


async def test_never_returns_headlines_from_after_the_decision(pool):
    as_of = datetime(2022, 9, 13, 16, tzinfo=timezone.utc)  # noon in New York
    got = await HeadlineRetriever(pool, "fts").around(SYMBOL, NY_DAY, as_of, limit=50)
    assert all(h.published_at < as_of for h in got)
    assert {h.headline for h in got} == {
        "12 Stocks to Buy Now",
        "Apple falls as hot CPI inflation data hits tech",
    }


# --------------------------------------------------------------------------
# Wired into the real graph: PM proposes a BUY, the VaR budget cuts it, and
# the explainer fires on that decision with the risk node's real var_tail.
# --------------------------------------------------------------------------

from app.agent.graph import build_decision_graph  # noqa: E402
from app.agent.sentiment_analyst import SentimentScore  # noqa: E402
from tests.agent_fakes import FakeHeadlineSource, FakeLLM, FakeRepository  # noqa: E402
from tests.test_risk_manager import HIGH_VOL, SymbolPriceSource, _bars  # noqa: E402


async def test_graph_runs_explainer_after_a_var_cut():
    retriever = FakeRetriever({})  # no headlines anywhere
    explainer_llm = RecordingLLM(_call([]))
    graph = build_decision_graph(
        price_source=SymbolPriceSource({"AAPL": _bars("AAPL", HIGH_VOL)}),
        headline_source=FakeHeadlineSource([]),
        repository=FakeRepository(),
        sentiment_llm=FakeLLM(SentimentScore(score=0.0, confidence=0.5, reasoning="t")),
        portfolio_llm=FakeLLM(TentativeDecision(action="BUY", quantity=10, confidence=0.8, reasoning="t")),
        explainer_retriever=retriever,
        explainer_llm=explainer_llm,
    )
    state = await graph.ainvoke({"symbol": "AAPL", "as_of": AS_OF})

    assert state["risk_verdict"].opinion == "SCALE"  # VaR 8% -> at most 5 shares
    e = state["risk_explanation"]
    assert e is not None and e.trigger_flags == ["var_budget_scale"]
    # It explained the risk node's own worst-loss days, worst first.
    assert list(e.retrieved) == [t["date"] for t in state["risk_opinion"].raw_output["var_tail"][:DRIVER_DAYS]]
    assert "(no headlines on record)" in explainer_llm.messages[1]["content"]
    # An empty answer omits every driver day, which the check reports.
    assert not e.citations_valid and e.validation_problems[0].startswith("no driver entry for")


async def test_graph_without_retriever_has_no_explainer():
    graph = build_decision_graph(
        price_source=SymbolPriceSource({"AAPL": _bars("AAPL", HIGH_VOL)}),
        headline_source=FakeHeadlineSource([]),
        repository=FakeRepository(),
        sentiment_llm=FakeLLM(SentimentScore(score=0.0, confidence=0.5, reasoning="t")),
        portfolio_llm=FakeLLM(TentativeDecision(action="BUY", quantity=10, confidence=0.8, reasoning="t")),
    )
    state = await graph.ainvoke({"symbol": "AAPL", "as_of": AS_OF})
    assert state["risk_verdict"].opinion == "SCALE"
    assert "risk_explanation" not in state


def test_template_singular_share():
    assert "cut the buy to 1 share:" in template_explanation(RAW, "SCALE", 1)
