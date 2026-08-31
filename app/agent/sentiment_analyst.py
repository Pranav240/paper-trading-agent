"""
Sentiment Analyst node — LLM-backed, unlike the Technical Analyst.

Why this one gets an LLM and RSI/SMA didn't (cost-control decision from
Phase 03 planning): reading a batch of headlines and judging whether they
lean bullish/bearish for a stock is a genuine language-understanding
judgment call, not arithmetic. That's exactly the kind of task an LLM is
worth paying for.

Sandbox note: the original plan was a locally fine-tuned FinBERT model
(that's still the real Phase 04 plan). This sandbox's network policy
blocks huggingface.co (model weights can't be downloaded here), so this
node calls GPT-4o-mini via API instead — a workaround for THIS
environment, not a permanent design decision. Swapping back to a local
model later only touches this file, because the node's contract
(HeadlineSource in, AgentOpinion out) doesn't change either way.

Cost control still applies even with an LLM: if there are no headlines at
all, there's nothing for a model to read, so we return HOLD without
spending a single token on the API call.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.agent.data_sources import HeadlineSource
from app.agent.state import AgentOpinion, GraphState

SYSTEM_PROMPT = """You are a sentiment analyst for a stock paper-trading \
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


def make_sentiment_analyst_node(
    headline_source: HeadlineSource,
    llm: ChatOpenAI | None = None,
) -> Callable[[GraphState], Awaitable[dict]]:
    # `llm` is injectable so tests can pass a fake/mock instead of hitting
    # the OpenAI API — same reason price_source/headline_source are
    # injected rather than constructed inside the node. The DEFAULT
    # client, though, is built lazily inside node() rather than here in
    # the factory: building it eagerly would mean every call to this
    # factory requires OPENAI_API_KEY to be set, even for symbols that
    # turn out to have zero headlines and never actually need the LLM —
    # defeating the whole "skip the API call when there's nothing to
    # read" cost-control point below.
    async def node(state: GraphState) -> dict:
        symbol = state["symbol"]
        as_of = state["as_of"]

        headlines = await headline_source.get_recent_headlines(
            symbol, as_of, lookback_days=3
        )

        if not headlines:
            opinion = AgentOpinion(
                agent_name="sentiment_analyst",
                opinion="HOLD",
                confidence=None,
                reasoning=f"No headlines found for {symbol} in the last 3 days.",
                raw_output={"n_headlines": 0},
            )
            return {"sentiment_opinion": opinion}

        model = llm or ChatOpenAI(model="gpt-4o-mini", temperature=0)
        structured_model = model.with_structured_output(SentimentCall)

        headline_block = "\n".join(f"- {h.headline}" for h in headlines)
        user_prompt = (
            f"Symbol: {symbol}\n"
            f"Headlines ({len(headlines)}):\n{headline_block}"
        )

        call: SentimentCall = await structured_model.ainvoke(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
        )

        opinion = AgentOpinion(
            agent_name="sentiment_analyst",
            opinion=call.opinion,
            confidence=call.confidence,
            reasoning=call.reasoning,
            raw_output={
                "n_headlines": len(headlines),
                "headlines": [h.headline for h in headlines],
            },
        )
        return {"sentiment_opinion": opinion}

    return node
