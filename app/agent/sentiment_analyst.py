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
all, there's nothing for a model to read, so we return a neutral score
without spending a single token on the API call.

WHY A CONTINUOUS SCORE AND NOT A BUY/HOLD/SELL VOTE (Phase 04, 2026-09-08)
-------------------------------------------------------------------------
This node used to emit a categorical BUY/HOLD/SELL vote. Two findings
killed that design, and the continuous score addresses both at once:

1. The categorical version said HOLD on 96.8% of 557 real calls. Every
   attempt to fine-tune a local model to imitate it collapsed to the
   constant "always HOLD" function — on a ~96%-one-class target, that IS
   the loss minimum. A score has real variance even on boring news, so it
   is a trainable regression target.
2. An ablation (backtests 4/9 with the node vs 7/8 with it stubbed out)
   showed the categorical node was a net NEGATIVE: replacing it with a
   fixed always-HOLD stand-in *improved* mark-to-market P&L by ~60 over a
   13-month AAPL window. The mechanism was visible in the action counts —
   its constant HOLD votes read to the Portfolio Manager as a vote
   against trading, suppressing ~9 BUYs and ~6 SELLs that would have been
   profitable over that window.

The old prompt's "prefer HOLD with lower confidence over guessing a
direction" line is what produced both problems, so it is deliberately
gone. A near-zero score now means "no directional information here",
which is honestly different from "I recommend not trading" — that
distinction is the whole point of the rewrite, and portfolio_manager.py's
prompt is written to read it that way.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.agent.data_sources import HeadlineSource
from app.agent.state import AgentOpinion, GraphState

SYSTEM_PROMPT = """You are a sentiment analyst for a stock paper-trading \
system. You will be given a list of recent news headlines for one \
symbol. Rate the overall tone of those headlines for the stock's \
near-term price on a continuous scale.

Rules:
- score is a number from -1.0 to +1.0, where the SIGN is the direction \
and the MAGNITUDE is how strongly the headlines lean that way:
    +1.0  unambiguously bullish (blowout results, major upgrade, \
transformative win)
    +0.5  clearly positive, but not dramatic
    +0.1 to +0.3  mildly or incidentally positive — routine news with a \
faintly favourable tilt
     0.0  genuinely tone-free, or positives and negatives that cancel out
    -0.1 to -0.3  mildly or incidentally negative
    -0.5  clearly negative
    -1.0  unambiguously bearish
- Small non-zero scores are expected and wanted. Most real news is \
routine, and routine news still usually carries a faint tilt — report \
that tilt instead of rounding it away. Reserve exactly 0.0 for headlines \
with no directional lean at all. Do NOT use 0.0 to avoid committing to a \
read.
- Base your judgment ONLY on the headlines given. Do not use outside \
knowledge of the company or assume information not present in the text.
- confidence is a SEPARATE axis from score: it is how sure you are of \
your read (0.0 to 1.0), not how strong the tone is. A faint but \
unmistakable tilt is a small score with HIGH confidence. Mixed, \
contradictory or ambiguous headlines are what deserve low confidence.
- reasoning must be 1-3 sentences, in plain English, that a human could \
audit against the headlines shown."""


class SentimentScore(BaseModel):
    """What the LLM actually returns — deliberately smaller than
    AgentOpinion (no agent_name, no raw_output) because those two fields
    are the node's job to fill in, not the model's.

    Replaces the old `SentimentCall` (a BUY/HOLD/SELL Literal) — see this
    module's docstring for why."""

    score: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


def format_score(score: float) -> str:
    """How a score is rendered into `agent_opinions.opinion`, which is
    free TEXT (verified: no CHECK constraint on that column, so this
    change needed no migration).

    Always signed, always 2dp, so "+0.00" and "-0.30" read and sort
    consistently — and so a numeric opinion is trivially distinguishable
    from the legacy BUY/HOLD/SELL rows backtests 3-9 left in the table."""
    return f"{score:+.2f}"


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
            # 0.0 here means "no information", not "recommend holding".
            # confidence=None is what marks it as the absence of a read
            # rather than a confidently neutral one.
            opinion = AgentOpinion(
                agent_name="sentiment_analyst",
                opinion=format_score(0.0),
                confidence=None,
                reasoning=f"No headlines found for {symbol} in the last 3 days.",
                raw_output={"n_headlines": 0, "score": 0.0},
            )
            return {"sentiment_opinion": opinion}

        model = llm or ChatOpenAI(model="gpt-4o-mini", temperature=0)
        structured_model = model.with_structured_output(SentimentScore)

        headline_block = "\n".join(f"- {h.headline}" for h in headlines)
        user_prompt = (
            f"Symbol: {symbol}\n"
            f"Headlines ({len(headlines)}):\n{headline_block}"
        )

        call: SentimentScore = await structured_model.ainvoke(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
        )

        opinion = AgentOpinion(
            agent_name="sentiment_analyst",
            opinion=format_score(call.score),
            confidence=call.confidence,
            reasoning=call.reasoning,
            raw_output={
                # `score` is duplicated out of `opinion` on purpose: the
                # numeric form is what the score-distribution check and
                # the (future) regression fine-tune read, and pulling a
                # number out of JSONB beats parsing it back out of TEXT.
                "score": call.score,
                "n_headlines": len(headlines),
                "headlines": [h.headline for h in headlines],
            },
        )
        return {"sentiment_opinion": opinion}

    return node
