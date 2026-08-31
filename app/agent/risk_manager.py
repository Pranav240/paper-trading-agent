"""
Risk Manager node — rule-based, no LLM, same cost-control reasoning as
the Technical Analyst: "is this trade within a hard position limit" is
arithmetic against a number the system already owns (list_positions),
not a judgment call worth paying an LLM for.

This is the last node before END, and it's the one that decides what
actually gets written to `outcomes` — GraphState's `final_action` /
`final_quantity` (what runner.py's paper-trade execution reads) are set
HERE, not by the Portfolio Manager. That split matters: the Portfolio
Manager proposes, this node has the actual authority to shrink or block
it. A bug in the Portfolio Manager's LLM output (e.g. proposing to sell
more shares than are held) gets caught here before it ever reaches a
database write.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from app.agent.state import AgentOpinion, GraphState, RiskVerdict
from app.repository.base import Repository

# Hard cap on shares held per symbol. Same "flat number, not % of a
# portfolio" simplification as DEFAULT_TRADE_QTY in portfolio_manager.py
# — there's no capital-allocation model in V1, so this is a position-size
# limit, not a risk-budget calculation.
MAX_POSITION_QTY = 20


def make_risk_manager_node(
    repository: Repository,
) -> Callable[[GraphState], Awaitable[dict]]:
    async def node(state: GraphState) -> dict:
        symbol = state["symbol"]
        tentative = state["tentative_decision"]

        positions = await repository.list_positions(symbol)
        current_qty = positions[0].quantity if positions else 0

        if tentative.action == "HOLD" or tentative.quantity == 0:
            verdict = RiskVerdict(
                opinion="APPROVE",
                reasoning="No trade proposed — nothing to gate.",
            )
            final_action, final_quantity = "HOLD", 0

        elif tentative.action == "BUY":
            projected_qty = current_qty + tentative.quantity
            if current_qty >= MAX_POSITION_QTY:
                verdict = RiskVerdict(
                    opinion="VETO",
                    reasoning=(
                        f"Already holding {current_qty} shares of {symbol}, "
                        f"at or above the {MAX_POSITION_QTY}-share position "
                        "limit — no further buying regardless of conviction."
                    ),
                )
                final_action, final_quantity = "HOLD", 0
            elif projected_qty > MAX_POSITION_QTY:
                allowed_qty = MAX_POSITION_QTY - current_qty
                verdict = RiskVerdict(
                    opinion="SCALE",
                    reasoning=(
                        f"Requested BUY {tentative.quantity} would bring "
                        f"{symbol} to {projected_qty} shares, over the "
                        f"{MAX_POSITION_QTY}-share limit — scaled down to "
                        f"{allowed_qty}."
                    ),
                    adjusted_quantity=allowed_qty,
                )
                final_action, final_quantity = "BUY", allowed_qty
            else:
                verdict = RiskVerdict(
                    opinion="APPROVE",
                    reasoning=(
                        f"BUY {tentative.quantity} keeps {symbol} at "
                        f"{projected_qty}/{MAX_POSITION_QTY} shares — "
                        "within the position limit."
                    ),
                )
                final_action, final_quantity = "BUY", tentative.quantity

        else:  # tentative.action == "SELL"
            if current_qty == 0:
                verdict = RiskVerdict(
                    opinion="VETO",
                    reasoning=(
                        f"No open position in {symbol} to sell — vetoing "
                        "rather than allowing a short position, which this "
                        "system doesn't model."
                    ),
                )
                final_action, final_quantity = "HOLD", 0
            elif tentative.quantity > current_qty:
                verdict = RiskVerdict(
                    opinion="SCALE",
                    reasoning=(
                        f"Requested SELL {tentative.quantity} exceeds the "
                        f"{current_qty} shares of {symbol} actually held — "
                        f"scaled down to {current_qty} (full close)."
                    ),
                    adjusted_quantity=current_qty,
                )
                final_action, final_quantity = "SELL", current_qty
            else:
                verdict = RiskVerdict(
                    opinion="APPROVE",
                    reasoning=(
                        f"SELL {tentative.quantity} is within the "
                        f"{current_qty} shares of {symbol} held."
                    ),
                )
                final_action, final_quantity = "SELL", tentative.quantity

        risk_opinion = AgentOpinion(
            agent_name="risk_manager",
            opinion=verdict.opinion,
            confidence=None,
            reasoning=verdict.reasoning,
            raw_output={
                "current_qty": current_qty,
                "requested_action": tentative.action,
                "requested_quantity": tentative.quantity,
                "max_position_qty": MAX_POSITION_QTY,
            },
        )

        return {
            "risk_verdict": verdict,
            "risk_opinion": risk_opinion,
            "final_action": final_action,
            "final_quantity": final_quantity,
        }

    return node
