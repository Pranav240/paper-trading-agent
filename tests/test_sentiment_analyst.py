from datetime import datetime

import pytest

from app.agent.data_sources import Headline
from app.agent.sentiment_analyst import SentimentScore, make_sentiment_analyst_node
from tests.agent_fakes import FakeHeadlineSource, FakeLLM


@pytest.mark.asyncio
async def test_no_headlines_returns_neutral_score_without_calling_llm():
    # No llm passed AND no headlines — if the node tried to build a real
    # ChatOpenAI() here (no API key set in this test env), construction
    # or the call would blow up. The test passing proves the cost-control
    # short-circuit actually skips the LLM entirely.
    node = make_sentiment_analyst_node(FakeHeadlineSource([]))
    result = await node({"symbol": "AAPL", "as_of": datetime(2026, 1, 1)})

    opinion = result["sentiment_opinion"]
    assert opinion.opinion == "+0.00"
    assert opinion.raw_output["score"] == 0.0
    # confidence None (not 0.0) is what distinguishes "no read was made"
    # from "a confident read that the news is neutral".
    assert opinion.confidence is None
    assert opinion.raw_output["n_headlines"] == 0


@pytest.mark.asyncio
async def test_headlines_present_uses_llm_score_and_logs_them():
    headlines = [
        Headline(
            symbol="AAPL",
            published_at=datetime(2026, 1, 1),
            headline="Apple beats earnings expectations",
            source="test-wire",
        ),
    ]
    fake_llm = FakeLLM(
        SentimentScore(score=0.65, confidence=0.7, reasoning="Strong earnings beat.")
    )
    node = make_sentiment_analyst_node(FakeHeadlineSource(headlines), llm=fake_llm)
    result = await node({"symbol": "AAPL", "as_of": datetime(2026, 1, 1)})

    opinion = result["sentiment_opinion"]
    assert opinion.opinion == "+0.65"
    assert opinion.raw_output["score"] == 0.65
    assert opinion.confidence == 0.7
    assert opinion.raw_output["n_headlines"] == 1
    assert opinion.raw_output["headlines"] == ["Apple beats earnings expectations"]


@pytest.mark.asyncio
async def test_negative_score_is_written_with_an_explicit_sign():
    # The signed 2dp format is what makes the new numeric opinions
    # distinguishable from the legacy BUY/HOLD/SELL rows still in
    # agent_opinions, so it's worth pinning in a test.
    headlines = [
        Headline(
            symbol="AAPL",
            published_at=datetime(2026, 1, 1),
            headline="Apple faces antitrust probe",
            source="test-wire",
        ),
    ]
    fake_llm = FakeLLM(
        SentimentScore(score=-0.3, confidence=0.55, reasoning="Regulatory overhang.")
    )
    node = make_sentiment_analyst_node(FakeHeadlineSource(headlines), llm=fake_llm)
    result = await node({"symbol": "AAPL", "as_of": datetime(2026, 1, 1)})

    assert result["sentiment_opinion"].opinion == "-0.30"


def test_score_is_bounded_to_the_documented_range():
    # The Portfolio Manager's prompt tells the model the score lives on
    # -1.0..+1.0; the schema is what actually enforces that, so a
    # structured-output response outside the range is rejected rather
    # than silently passed on as a much louder signal than intended.
    with pytest.raises(ValueError):
        SentimentScore(score=1.5, confidence=0.5, reasoning="too bullish")
    with pytest.raises(ValueError):
        SentimentScore(score=-1.5, confidence=0.5, reasoning="too bearish")
