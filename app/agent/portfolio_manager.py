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
            f"Sentiment Analyst opinion: {sentiment.opinion} "
            f"(confidence={sentiment.confidence})\n"
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
                "sentiment_opinion": sentiment.opinion,
            },
        )

        return {
            "tentative_decision": decision,
            "portfolio_opinion": portfolio_opinion,
        }

    return node
