"""
Shared types for the LangGraph flow.

`GraphState` is a TypedDict, not a Pydantic model, because that's what
LangGraph's StateGraph expects: each node is a function that takes the
current state dict and returns a partial dict of updates, which LangGraph
merges into the running state. Using TypedDict here is about matching
the framework's contract, not a step down from Pydantic — the values
INSIDE the state (AgentOpinion, TentativeDecision, RiskVerdict) are still
Pydantic models, so they're still validated.

Two agents run in parallel (Technical Analyst, Sentiment Analyst) and
write to different keys (`technical_opinion`, `sentiment_opinion`), so
there's no write conflict for LangGraph to resolve — this is the simple
case of parallel fan-out. If two nodes ever needed to write the SAME key
concurrently, that's when you'd reach for LangGraph's reducer/Annotated
mechanism; not needed here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypedDict

from pydantic import BaseModel


class AgentOpinion(BaseModel):
    """Matches the shape of the `agent_opinions` table exactly — this
    model is what gets written there, row for row."""

    agent_name: Literal[
        "technical_analyst", "sentiment_analyst", "risk_manager", "portfolio_manager"
    ]
    opinion: str
    confidence: float | None = None
    reasoning: str
    raw_output: dict = {}


class TentativeDecision(BaseModel):
    """The Portfolio Manager's proposal, before the Risk Manager reviews it."""

    action: Literal["BUY", "SELL", "HOLD"]
    quantity: int
    confidence: float
    reasoning: str


class RiskVerdict(BaseModel):
    """The Risk Manager's ruling on the tentative decision.

    `adjusted_quantity` is set only when opinion == "SCALE" — the Risk
    Manager approved the trade direction but shrank the size.
    """

    opinion: Literal["APPROVE", "VETO", "SCALE"]
    reasoning: str
    adjusted_quantity: int | None = None


class GraphState(TypedDict, total=False):
    symbol: str
    as_of: datetime

    technical_opinion: AgentOpinion
    sentiment_opinion: AgentOpinion

    tentative_decision: TentativeDecision
    portfolio_opinion: AgentOpinion  # loggable record of the PM's own opinion

    risk_verdict: RiskVerdict
    risk_opinion: AgentOpinion  # loggable record of the Risk Manager's opinion

    final_action: Literal["BUY", "SELL", "HOLD"]
    final_quantity: int
