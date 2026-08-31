from datetime import datetime

import pytest

from app.agent.data_sources import Headline
from app.agent.sentiment_analyst import SentimentCall, make_sentiment_analyst_node
from tests.agent_fakes import FakeHeadlineSource, FakeLLM


@pytest.mark.asyncio
async def test_no_headlines_returns_hold_without_calling_llm():
    # No llm passed AND no headlines — if the node tried to build a real
    # ChatOpenAI() here (no API key set in this test env), construction
    # or the call would blow up. The test passing proves the cost-control
    # short-circuit actually skips the LLM entirely.
    node = make_sentiment_analyst_node(FakeHeadlineSource([]))
    result = await node({"symbol": "AAPL", "as_of": datetime(2026, 1, 1)})

    opinion = result["sentiment_opinion"]
    assert opinion.opinion == "HOLD"
    assert opinion.confidence is None
    assert opinion.raw_output["n_headlines"] == 0


@pytest.mark.asyncio
async def test_headlines_present_uses_llm_verdict_and_logs_them():
    headlines = [
        Headline(
            symbol="AAPL",
            published_at=datetime(2026, 1, 1),
            headline="Apple beats earnings expectations",
            source="test-wire",
        ),
    ]
    fake_llm = FakeLLM(
        SentimentCall(opinion="BUY", confidence=0.7, reasoning="Strong earnings beat.")
    )
    node = make_sentiment_analyst_node(FakeHeadlineSource(headlines), llm=fake_llm)
    result = await node({"symbol": "AAPL", "as_of": datetime(2026, 1, 1)})

    opinion = result["sentiment_opinion"]
    assert opinion.opinion == "BUY"
    assert opinion.confidence == 0.7
    assert opinion.raw_output["n_headlines"] == 1
    assert opinion.raw_output["headlines"] == ["Apple beats earnings expectations"]
