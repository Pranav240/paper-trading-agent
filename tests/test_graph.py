"""
End-to-end test of the compiled StateGraph (app/agent/graph.py) with every
dependency faked — no Alpaca, no OpenAI, no Postgres. This is the one test
that actually exercises the fan-out/fan-in wiring: both analysts running
and handing off into the Portfolio Manager, which hands off into the Risk
Manager, which sets the keys runner.py reads (final_action/final_quantity).

Technical Analyst's rsi/sma are monkeypatched (same technique as
test_technical_analyst.py) so the scenario is deterministic without
having to reverse-engineer a bar sequence that produces a specific RSI.
"""

from datetime import datetime
from decimal import Decimal

import pytest

import app.agent.technical_analyst as ta_module
from app.agent.data_sources import Headline, PriceBar
from app.agent.graph import build_decision_graph
from app.agent.portfolio_manager import DEFAULT_TRADE_QTY
from app.agent.sentiment_analyst import SentimentCall
from app.agent.state import TentativeDecision
from app.agent.risk_manager import MAX_POSITION_QTY
from tests.agent_fakes import (
    FakeHeadlineSource,
    FakeLLM,
    FakePriceSource,
    FakeRepository,
    make_position,
)


def _bar():
    return PriceBar(
        symbol="AAPL",
        timestamp=datetime(2026, 1, 1),
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        volume=1000,
    )


def _headline():
    return Headline(
        symbol="AAPL",
        published_at=datetime(2026, 1, 1),
        headline="Apple announces new product line",
        source="test-wire",
    )


@pytest.mark.asyncio
async def test_full_pipeline_approves_buy_when_within_position_limit(monkeypatch):
    monkeypatch.setattr(ta_module, "rsi", lambda closes, period=14: Decimal("25"))
    monkeypatch.setattr(ta_module, "sma", lambda closes, period: Decimal("95"))

    graph = build_decision_graph(
        price_source=FakePriceSource([_bar()]),
        headline_source=FakeHeadlineSource([_headline()]),
        repository=FakeRepository(),  # no existing position
        sentiment_llm=FakeLLM(
            SentimentCall(opinion="BUY", confidence=0.6, reasoning="Positive news.")
        ),
        portfolio_llm=FakeLLM(
            TentativeDecision(
                action="BUY",
                quantity=DEFAULT_TRADE_QTY,
                confidence=0.75,
                reasoning="Technical and sentiment both bullish.",
            )
        ),
    )

    result = await graph.ainvoke({"symbol": "AAPL", "as_of": datetime(2026, 1, 1)})

    assert result["technical_opinion"].opinion == "BUY"
    assert result["sentiment_opinion"].opinion == "BUY"
    assert result["tentative_decision"].action == "BUY"
    assert result["risk_verdict"].opinion == "APPROVE"
    assert result["final_action"] == "BUY"
    assert result["final_quantity"] == DEFAULT_TRADE_QTY


@pytest.mark.asyncio
async def test_full_pipeline_vetoes_buy_when_position_already_at_cap(monkeypatch):
    monkeypatch.setattr(ta_module, "rsi", lambda closes, period=14: Decimal("25"))
    monkeypatch.setattr(ta_module, "sma", lambda closes, period: Decimal("95"))

    graph = build_decision_graph(
        price_source=FakePriceSource([_bar()]),
        headline_source=FakeHeadlineSource([_headline()]),
        repository=FakeRepository([make_position("AAPL", quantity=MAX_POSITION_QTY)]),
        sentiment_llm=FakeLLM(
            SentimentCall(opinion="BUY", confidence=0.6, reasoning="Positive news.")
        ),
        portfolio_llm=FakeLLM(
            TentativeDecision(
                action="BUY",
                quantity=DEFAULT_TRADE_QTY,
                confidence=0.75,
                reasoning="Technical and sentiment both bullish.",
            )
        ),
    )

    result = await graph.ainvoke({"symbol": "AAPL", "as_of": datetime(2026, 1, 1)})

    # The Portfolio Manager still proposed BUY (its job stops at proposing)
    # — it's the Risk Manager that overrides it because of the position cap.
    assert result["tentative_decision"].action == "BUY"
    assert result["risk_verdict"].opinion == "VETO"
    assert result["final_action"] == "HOLD"
    assert result["final_quantity"] == 0
