"""
Portfolio Manager node — LLM-backed synthesis, not a vote-counter.

Why this needs an LLM and isn't just "if both agree, do that; else HOLD":
the two specialists can disagree, or agree but for weak reasons, or one
can be far more confident than the other — weighing that is a judgment
call, the same category of task the Sentiment Analyst does. A rule-based
synthesis would need to hand-enumerate every disagreement case; an LLM
reading both opinions' reasoning (not just their labels) can weigh "RSI
oversold, confidence 0.65" against "headlines mixed, confidence 0.3"
the way a human analyst reading two reports would.

This node does NOT get final say — `tentative_decision` is exactly that,
tentative. risk_manager.py reviews it next and can veto or scale it down.
That split (propose vs. gate) is why there are two separate LLM/logic
nodes here instead of one node doing both jobs.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from langchain_openai import ChatOpenAI

from app.agent.state import AgentOpinion, GraphState, TentativeDecision

# Fixed paper-trade lot size. Keeping this a flat number (rather than a
# % of some notional portfolio value, which doesn't exist yet — there's
# no capital-allocation model in V1) is a deliberate simplification:
# it's enough to test whether the decision LOGIC has an edge, which is
# the actual V1 goal, without also having to design position sizing.
DEFAULT_TRADE_QTY = 10

SYSTEM_PROMPT = f"""You are the Portfolio Manager in a paper-trading \
decision system. You will be given two specialist opinions on one stock: \
a Technical Analyst (price/indicator based, who votes BUY/SELL/HOLD) and \
a Sentiment Analyst (news based, who returns a numeric tone score rather \
than a vote). Synthesize them into ONE tentative trading decision.

How to read the sentiment score:
- It runs from -1.0 (clearly bearish headlines) to +1.0 (clearly bullish \
headlines). The sign is the direction; the magnitude is how strongly the \
news leans that way.
- A score near 0.0 means the news carried NO directional information. \
That is not a vote against trading and not an argument for HOLD — it \
simply means this decision rests on the technical read alone. Treat it as \
silence, not as opposition.
- Magnitudes around 0.1-0.3 are normal for routine news and are real, \
mild evidence — weak, but pointing somewhere.
- The Sentiment Analyst's confidence is a separate axis from the score: \
it says how sure the read is, not how strong the tone is. A small score \
with high confidence is a reliable weak signal; a large score with low \
confidence is a loud but unreliable one.

Rules:
- Weigh both specialists' reasoning, magnitude and confidence, not just \
their headline numbers. Two weak/uncertain signals pointing the same \
direction do not automatically outweigh one strong, well-reasoned signal \
pointing the other way.
- If the two specialists genuinely conflict — the sentiment score is \
meaningfully non-zero, points the opposite way to the Technical \
Analyst's vote, and both are similarly confident — prefer HOLD. This \
system's whole point is honest evaluation, not forcing a trade out of two \
mediocre signals. A near-zero sentiment score is NOT such a conflict.
- action must be BUY, SELL, or HOLD.
- quantity must be 0 if action is HOLD, otherwise a whole number of \
shares between 1 and {DEFAULT_TRADE_QTY} (this system trades in a fixed \
maximum lot size of {DEFAULT_TRADE_QTY} shares — you decide whether to \
use the full size or a smaller size based on your conviction, not \
whether to exceed it).
- confidence must be a number between 0.0 and 1.0, reflecting YOUR \
combined conviction, not a copy of either specialist's number.
- reasoning must explain how you weighed the two opinions, in plain \
English a human could audit."""


def make_portfolio_manager_node(
    llm: ChatOpenAI | None = None,
) -> Callable[[GraphState], Awaitable[dict]]:
    model = llm or ChatOpenAI(model="gpt-4o", temperature=0)
    structured_model = model.with_structured_output(TentativeDecision)

    async def node(state: GraphState) -> dict:
        technical = state["technical_opinion"]
        sentiment = state["sentiment_opinion"]
        symbol = state["symbol"]

        user_prompt = (
            f"Symbol: {symbol}\n\n"
            f"Technical Analyst opinion: {technical.opinion} "
            f"(confidence={technical.confidence})\n"
            f"Technical Analyst reasoning: {technical.reasoning}\n\n"
            f"Sentiment Analyst score: {sentiment.opinion} "
            f"(on the -1.0 to +1.0 scale; confidence={sentiment.confidence})\n"
            f"Sentiment Analyst reasoning: {sentiment.reasoning}"
        )

        decision: TentativeDecision = await structured_model.ainvoke(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
        )

        # Logged as its own agent_opinions row — same reasoning as
        # storing risk_manager's ruling separately from the final action:
        # "what did the Portfolio Manager think" should be answerable
        # even after the Risk Manager overrides it downstream.
        portfolio_opinion = AgentOpinion(
            agent_name="portfolio_manager",
            opinion=decision.action,
            confidence=decision.confidence,
            reasoning=decision.reasoning,
            raw_output={
                "quantity": decision.quantity,
                "technical_opinion": technical.opinion,
                # Kept under the same key as before the score rewrite so
                # existing dashboard/export queries don't break; it now
                # holds a signed decimal string ("+0.30") rather than
                # BUY/HOLD/SELL. `sentiment_score` is the numeric form,
                # for anything that wants to do arithmetic on it.
                "sentiment_opinion": sentiment.opinion,
                "sentiment_score": sentiment.raw_output.get("score"),
            },
        )

        return {
            "tentative_decision": decision,
            "portfolio_opinion": portfolio_opinion,
        }

    return node
