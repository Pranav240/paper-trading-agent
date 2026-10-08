"""
V1's categorical sentiment mode (app/agent/v1_categorical.py), restored
for faithful reruns of backtests #3, #4, #6, #9.

The prompt pins are the point: these texts were checked byte-for-byte
against git (ba47f0a for sentiment, 275b6e4^ for the Portfolio Manager)
when restored. If either changes, a "V1 rerun" silently stops being one,
so the test fails instead.
"""

import hashlib
from datetime import datetime

from app.agent.data_sources import Headline
from app.agent.portfolio_manager import SYSTEM_PROMPT as SCORE_PM_PROMPT
from app.agent.portfolio_manager import make_portfolio_manager_node
from app.agent.sentiment_analyst import make_sentiment_analyst_node
from app.agent.state import AgentOpinion, TentativeDecision
from app.agent.v1_categorical import (
    CATEGORICAL_PM_PROMPT,
    CATEGORICAL_SENTIMENT_PROMPT,
    SentimentCall,
)
from tests.agent_fakes import FakeHeadlineSource


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class RecordingLLM:
    """Fake chat model that records the schema and messages it was given."""

    def __init__(self, response):
        self.response, self.schema, self.messages = response, None, None

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    async def ainvoke(self, messages):
        self.messages = messages
        return self.response


def test_prompts_are_the_v1_originals():
    assert _sha(CATEGORICAL_SENTIMENT_PROMPT) == "2fedf642fa37444e"  # ba47f0a
    assert _sha(CATEGORICAL_PM_PROMPT) == "5497ba5585ca5524"  # 275b6e4^


async def test_categorical_sentiment_with_no_headlines_is_a_hold_vote():
    node = make_sentiment_analyst_node(FakeHeadlineSource([]), mode="categorical")
    opinion = (await node({"symbol": "AAPL", "as_of": datetime(2023, 1, 3)}))["sentiment_opinion"]

    assert opinion.opinion == "HOLD"
    assert opinion.confidence is None
    assert opinion.raw_output == {"n_headlines": 0}


async def test_categorical_sentiment_votes_with_v1_prompt_and_schema():
    headlines = [Headline(symbol="AAPL", published_at=datetime(2023, 1, 2),
                          headline="Apple beats estimates", source="t")]
    llm = RecordingLLM(SentimentCall(opinion="BUY", confidence=0.7, reasoning="beat"))
    node = make_sentiment_analyst_node(FakeHeadlineSource(headlines), llm=llm, mode="categorical")
    opinion = (await node({"symbol": "AAPL", "as_of": datetime(2023, 1, 3)}))["sentiment_opinion"]

    assert llm.schema is SentimentCall
    assert llm.messages[0] == {"role": "system", "content": CATEGORICAL_SENTIMENT_PROMPT}
    assert llm.messages[1]["content"] == "Symbol: AAPL\nHeadlines (1):\n- Apple beats estimates"
    assert (opinion.opinion, opinion.confidence) == ("BUY", 0.7)
    assert "score" not in opinion.raw_output


def _pm_state(sentiment_opinion: str):
    return {
        "symbol": "AAPL",
        "technical_opinion": AgentOpinion(
            agent_name="technical_analyst", opinion="BUY", confidence=0.6, reasoning="RSI low"
        ),
        "sentiment_opinion": AgentOpinion(
            agent_name="sentiment_analyst", opinion=sentiment_opinion, confidence=0.5,
            reasoning="mixed",
        ),
    }


async def test_categorical_pm_uses_v1_prompt_and_wording():
    llm = RecordingLLM(TentativeDecision(action="HOLD", quantity=0, confidence=0.5, reasoning="t"))
    node = make_portfolio_manager_node(llm=llm, sentiment_mode="categorical")
    await node(_pm_state("HOLD"))

    assert llm.messages[0] == {"role": "system", "content": CATEGORICAL_PM_PROMPT}
    assert "Sentiment Analyst opinion: HOLD (confidence=0.5)" in llm.messages[1]["content"]
    assert "-1.0 to +1.0" not in llm.messages[1]["content"]


async def test_score_mode_is_unchanged():
    llm = RecordingLLM(TentativeDecision(action="HOLD", quantity=0, confidence=0.5, reasoning="t"))
    node = make_portfolio_manager_node(llm=llm)
    await node(_pm_state("+0.10"))

    assert llm.messages[0] == {"role": "system", "content": SCORE_PM_PROMPT}
    assert "Sentiment Analyst score: +0.10 (on the -1.0 to +1.0 scale" in llm.messages[1]["content"]
