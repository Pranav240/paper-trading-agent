"""
V1's categorical sentiment setup, restored so V1 backtests can be rerun
faithfully (V2, phase 07). Not for new work.

Backtests #3, #4, #6 and #9 ran with a Sentiment Analyst that voted
BUY/SELL/HOLD, and a Portfolio Manager prompt written to read that vote.
Phase 04 replaced both (commit 275b6e4: the vote said HOLD 96.8% of the
time and measurably suppressed trades). Rerunning those backtests with the
current score-based prompts would be a different experiment, so the
originals are kept here verbatim:

- CATEGORICAL_SENTIMENT_PROMPT and SentimentCall: from ba47f0a,
  app/agent/sentiment_analyst.py.
- CATEGORICAL_PM_PROMPT and categorical_pm_user_prompt: from 275b6e4^,
  app/agent/portfolio_manager.py.

tests/test_v1_categorical.py pins their exact text, so an edit here fails
loudly instead of quietly changing what a "V1 rerun" means.

Selected with `sentiment_mode="categorical"` on build_decision_graph
(scripts/run_backtest.py --sentiment-mode categorical), and recorded in
backtests.config.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.agent.state import AgentOpinion

SentimentMode = Literal["score", "categorical"]

# Must match portfolio_manager.DEFAULT_TRADE_QTY, as it did in V1.
_V1_TRADE_QTY = 10

CATEGORICAL_SENTIMENT_PROMPT = """You are a sentiment analyst for a stock paper-trading \
system. You will be given a list of recent news headlines for one \
symbol. Judge whether the overall tone of the headlines is bullish, \
bearish, or neutral for the stock's near-term price, and how confident \
you are in that read.

Rules:
- Base your judgment ONLY on the headlines given. Do not use outside \
knowledge of the company or assume information not present in the text.
- If headlines are mixed, contradictory, or mostly routine/non-market-moving, \
prefer HOLD with lower confidence over guessing a direction.
- confidence must be a number between 0.0 and 1.0.
- reasoning must be 1-3 sentences, in plain English, that a human could \
audit against the headlines shown."""


class SentimentCall(BaseModel):
    """What the LLM actually returns — deliberately smaller than
    AgentOpinion (no agent_name, no raw_output) because those two fields
    are the node's job to fill in, not the model's."""

    opinion: Literal["BUY", "SELL", "HOLD"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


CATEGORICAL_PM_PROMPT = f"""You are the Portfolio Manager in a paper-trading \
decision system. You will be given two specialist opinions on one stock: \
a Technical Analyst (price/indicator based) and a Sentiment Analyst \
(news based). Synthesize them into ONE tentative trading decision.

Rules:
- Weigh both opinions' reasoning and confidence, not just their labels. \
Two weak/uncertain opinions pointing the same direction do not \
automatically outweigh one strong, well-reasoned opinion pointing the \
other way.
- If the two specialists clearly conflict with similar confidence, \
prefer HOLD — this system's whole point is honest evaluation, not \
forcing a trade out of two mediocre signals.
- action must be BUY, SELL, or HOLD.
- quantity must be 0 if action is HOLD, otherwise a whole number of \
shares between 1 and {_V1_TRADE_QTY} (this system trades in a fixed \
maximum lot size of {_V1_TRADE_QTY} shares — you decide whether to \
use the full size or a smaller size based on your conviction, not \
whether to exceed it).
- confidence must be a number between 0.0 and 1.0, reflecting YOUR \
combined conviction, not a copy of either specialist's number.
- reasoning must explain how you weighed the two opinions, in plain \
English a human could audit."""


def categorical_pm_user_prompt(
    symbol: str, technical: AgentOpinion, sentiment: AgentOpinion
) -> str:
    return (
        f"Symbol: {symbol}\n\n"
        f"Technical Analyst opinion: {technical.opinion} "
        f"(confidence={technical.confidence})\n"
        f"Technical Analyst reasoning: {technical.reasoning}\n\n"
        f"Sentiment Analyst opinion: {sentiment.opinion} "
        f"(confidence={sentiment.confidence})\n"
        f"Sentiment Analyst reasoning: {sentiment.reasoning}"
    )


def no_headlines_opinion(symbol: str) -> AgentOpinion:
    """V1's zero-headline short-circuit: a HOLD vote, no LLM call."""
    return AgentOpinion(
        agent_name="sentiment_analyst",
        opinion="HOLD",
        confidence=None,
        reasoning=f"No headlines found for {symbol} in the last 3 days.",
        raw_output={"n_headlines": 0},
    )
