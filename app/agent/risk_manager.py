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

V2 (phase 07) adds a VaR budget on top. The V1 rules still run first and
unchanged; they are a hard floor. The budget can only shrink or block a
BUY that V1 already allowed, never enlarge anything. Pre-registered
before any backtest was run with it (docs/engineering-log.md, phase 07
step 3):

- Budget: a full MAX_POSITION_QTY position may carry at most VAR_BUDGET
  (2%) of its value as 95% 1-day historical VaR. So the most shares
  allowed is floor(MAX_POSITION_QTY * VAR_BUDGET / VaR), capped at
  MAX_POSITION_QTY. VaR 2% or less: the budget never binds.
- SELLs are never limited by the budget (selling only reduces risk).
- A position already over budget (volatility rose after buying) blocks
  further buys and is flagged, but is never force-sold.
- Fewer than 250 returns of history: V1 rules only, flagged.
- Correlation with other held symbols is a flag only, never a sizing input.

Without a price_source the node is exactly the V1 rule-based node.
"""

from __future__ import annotations

import math
from typing import Awaitable, Callable

from app.agent.data_sources import NEW_YORK, PriceDataSource
from app.agent.risk_math import (
    CONFIDENCE,
    CORRELATION_WINDOW,
    VAR_WINDOW,
    aligned_returns,
    correlation_matrix,
    historical_cvar,
    historical_var,
    parametric_cvar,
    parametric_var,
    simple_returns,
)
from app.agent.state import AgentOpinion, GraphState, RiskVerdict, TentativeDecision
from app.repository.base import Repository

# Hard cap on shares held per symbol. Same "flat number, not % of a
# portfolio" simplification as DEFAULT_TRADE_QTY in portfolio_manager.py
# — there's no capital-allocation model in V1, so this is a position-size
# limit, not a risk-budget calculation.
MAX_POSITION_QTY = 20

# Pre-registered (see module docstring). Do not tune after seeing results.
VAR_BUDGET = 0.02
CORRELATION_FLAG = 0.8

# Calendar days of bars to request: 250 returns need 251 trading days,
# about 365 calendar days; 400 leaves room for holidays.
VAR_LOOKBACK_DAYS = 400
CORRELATION_LOOKBACK_DAYS = 100


def _v1_rules(
    symbol: str, tentative: TentativeDecision, current_qty: int
) -> tuple[RiskVerdict, str, int]:
    """The V1 position-limit rules, unchanged. Returns (verdict, action, qty)."""
    if tentative.action == "HOLD" or tentative.quantity == 0:
        verdict = RiskVerdict(
            opinion="APPROVE",
            reasoning="No trade proposed — nothing to gate.",
        )
        return verdict, "HOLD", 0

    if tentative.action == "BUY":
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
            return verdict, "HOLD", 0
        if projected_qty > MAX_POSITION_QTY:
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
            return verdict, "BUY", allowed_qty
        verdict = RiskVerdict(
            opinion="APPROVE",
            reasoning=(
                f"BUY {tentative.quantity} keeps {symbol} at "
                f"{projected_qty}/{MAX_POSITION_QTY} shares — "
                "within the position limit."
            ),
        )
        return verdict, "BUY", tentative.quantity

    # tentative.action == "SELL"
    if current_qty == 0:
        verdict = RiskVerdict(
            opinion="VETO",
            reasoning=(
                f"No open position in {symbol} to sell — vetoing "
                "rather than allowing a short position, which this "
                "system doesn't model."
            ),
        )
        return verdict, "HOLD", 0
    if tentative.quantity > current_qty:
        verdict = RiskVerdict(
            opinion="SCALE",
            reasoning=(
                f"Requested SELL {tentative.quantity} exceeds the "
                f"{current_qty} shares of {symbol} actually held — "
                f"scaled down to {current_qty} (full close)."
            ),
            adjusted_quantity=current_qty,
        )
        return verdict, "SELL", current_qty
    verdict = RiskVerdict(
        opinion="APPROVE",
        reasoning=(
            f"SELL {tentative.quantity} is within the "
            f"{current_qty} shares of {symbol} held."
        ),
    )
    return verdict, "SELL", tentative.quantity


def var_max_qty(var: float) -> int:
    """Most shares the VaR budget allows, never above the hard cap."""
    if var <= 0:
        return MAX_POSITION_QTY
    # round() guards float noise, e.g. 20 * 0.02 / 0.025 = 15.999999999999998.
    return min(MAX_POSITION_QTY, math.floor(round(MAX_POSITION_QTY * VAR_BUDGET / var, 9)))


def _ny_closes(bars) -> dict:
    return {b.timestamp.astimezone(NEW_YORK).date(): float(b.close) for b in bars}


def make_risk_manager_node(
    repository: Repository,
    price_source: PriceDataSource | None = None,
) -> Callable[[GraphState], Awaitable[dict]]:
    async def node(state: GraphState) -> dict:
        symbol = state["symbol"]
        tentative = state["tentative_decision"]

        positions = await repository.list_positions(symbol)
        current_qty = positions[0].quantity if positions else 0

        verdict, final_action, final_quantity = _v1_rules(symbol, tentative, current_qty)

        raw_output = {
            "current_qty": current_qty,
            "requested_action": tentative.action,
            "requested_quantity": tentative.quantity,
            "max_position_qty": MAX_POSITION_QTY,
        }

        if price_source is not None:
            as_of = state["as_of"]
            bars = await price_source.get_recent_bars(
                symbol, as_of, lookback_days=VAR_LOOKBACK_DAYS
            )
            returns = simple_returns([b.close for b in bars])
            var = historical_var(returns)
            flags: list[str] = []
            raw_output.update(
                {
                    "var_confidence": CONFIDENCE,
                    "var_window": VAR_WINDOW,
                    "var_budget": VAR_BUDGET,
                    "returns_available": len(returns),
                    "var_95": var,
                    "cvar_95": historical_cvar(returns),
                    "parametric_var_95": parametric_var(returns),
                    "parametric_cvar_95": parametric_cvar(returns),
                    "last_close": float(bars[-1].close) if bars else None,
                }
            )

            if var is None:
                flags.append("var_unavailable")
                raw_output["var_max_qty"] = None
            else:
                max_qty = var_max_qty(var)
                raw_output["var_max_qty"] = max_qty
                if current_qty > max_qty:
                    flags.append("over_var_budget")
                if final_action == "BUY":
                    room = max_qty - current_qty
                    budget_note = (
                        f"95% 1-day VaR is {var:.2%}; a {VAR_BUDGET:.0%} budget on a "
                        f"{MAX_POSITION_QTY}-share position allows at most {max_qty} shares"
                    )
                    if room <= 0:
                        verdict = RiskVerdict(
                            opinion="VETO",
                            reasoning=(
                                f"{verdict.reasoning} VaR budget: {budget_note}, and "
                                f"{current_qty} are already held — no further buying."
                            ),
                        )
                        final_action, final_quantity = "HOLD", 0
                        flags.append("var_budget_veto")
                    elif final_quantity > room:
                        verdict = RiskVerdict(
                            opinion="SCALE",
                            reasoning=(
                                f"{verdict.reasoning} VaR budget: {budget_note} — "
                                f"BUY scaled to {room}."
                            ),
                            adjusted_quantity=room,
                        )
                        final_quantity = room
                        flags.append("var_budget_scale")

            # Correlation with the other symbols currently held: flag only.
            others = [
                p.symbol for p in await repository.list_positions()
                if p.symbol != symbol and p.quantity > 0
            ]
            correlations = {}
            if others:
                closes = {symbol: _ny_closes(bars)}
                for other in others:
                    closes[other] = _ny_closes(
                        await price_source.get_recent_bars(
                            other, as_of, lookback_days=CORRELATION_LOOKBACK_DAYS
                        )
                    )
                matrix = correlation_matrix(aligned_returns(closes)) or {}
                for (a, b), rho in matrix.items():
                    if symbol not in (a, b) or rho is None:
                        continue
                    other = b if a == symbol else a
                    correlations[other] = rho
                    if rho > CORRELATION_FLAG:
                        flags.append(f"high_correlation:{other}")
            raw_output["correlation_window"] = CORRELATION_WINDOW
            raw_output["correlations"] = correlations
            raw_output["risk_flags"] = flags

        risk_opinion = AgentOpinion(
            agent_name="risk_manager",
            opinion=verdict.opinion,
            confidence=None,
            reasoning=verdict.reasoning,
            raw_output=raw_output,
        )

        return {
            "risk_verdict": verdict,
            "risk_opinion": risk_opinion,
            "final_action": final_action,
            "final_quantity": final_quantity,
        }

    return node
