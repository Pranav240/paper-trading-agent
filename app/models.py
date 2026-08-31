"""
Pydantic schemas for the Control API.

These are NOT database models (there is no database yet — that's Phase 02).
They exist to answer one question: "what shape does data have when it
crosses the API boundary?" FastAPI uses these for two jobs at once:

1. Validation — a request body or query param that doesn't match the
   schema gets rejected with a 422 before your endpoint code ever runs.
2. Serialization + docs — the response_model on each route tells FastAPI
   exactly what JSON to emit, and it's also what generates the /docs
   schema so the interactive docs aren't guesswork.

We're deliberately using Decimal for money and timezone-aware datetimes,
because these are the two things that quietly bite you later: floats lose
precision on prices, and naive datetimes cause bugs the moment this thing
talks to a market that has a timezone (US markets, UTC feeds, etc).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Action(str, Enum):
    """The three things the decision agent is allowed to decide.

    Subclassing str + Enum means this behaves as a normal string on the
    wire (JSON gets "BUY", not some enum repr) while still giving us
    validation: anything that isn't one of these three values is rejected
    automatically by Pydantic, instead of failing silently deep in the
    agent logic later.
    """

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class RunStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class Position(BaseModel):
    """An open paper-trading position for one symbol.

    This is intentionally a *view* model: in Phase 02 this will be
    computed by joining decisions + outcomes in Postgres, not stored as
    its own table. Defining the shape now means that join has a concrete
    target to produce, instead of us discovering the right shape later.
    """

    model_config = ConfigDict(from_attributes=True)

    symbol: str
    quantity: int = Field(ge=0, description="Shares held, paper-trading only")
    avg_entry_price: Decimal
    # Optional, not a Decimal: the `positions` VIEW LEFT JOINs against
    # price_snapshots on purpose (see 002_positions_view.sql) precisely
    # so an open position with no recorded price yet still shows up
    # instead of vanishing from the query — a symbol's very first trade
    # is priced before that cycle's own price_snapshots row is written
    # (risk_manager checks current holdings mid-cycle, price gets
    # recorded after the cycle completes), so NULL here is a real,
    # legitimate state, not just missing data. Declaring these as
    # required Decimal fields was the actual bug behind a 500 on
    # GET /positions the first time this ran live — the view was always
    # allowed to return NULL, the model just never admitted it.
    current_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    opened_at: datetime


class Decision(BaseModel):
    """One row of the decision log: what the agent decided, and why.

    `reasoning`, `technicals_snapshot`, and `sentiment_snapshot` exist so
    that when this system is wrong, we can actually see why it was wrong,
    rather than just seeing "SELL" with no trail. That auditability is
    the entire point of a decision-support system as opposed to a black
    box.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    action: Action
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    technicals_snapshot: dict
    sentiment_snapshot: dict
    created_at: datetime


class RunResult(BaseModel):
    """The response for POST /run/trigger: one full agent cycle."""

    run_id: int
    started_at: datetime
    finished_at: datetime
    status: RunStatus
    decisions: list[Decision]
