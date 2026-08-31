import pytest

from app.agent.risk_manager import MAX_POSITION_QTY, make_risk_manager_node
from app.agent.state import TentativeDecision
from tests.agent_fakes import FakeRepository, make_position


def _state(symbol: str, action: str, quantity: int, confidence: float = 0.6):
    return {
        "symbol": symbol,
        "tentative_decision": TentativeDecision(
            action=action,
            quantity=quantity,
            confidence=confidence,
            reasoning="test",
        ),
    }


@pytest.mark.asyncio
async def test_hold_is_always_approved_with_zero_quantity():
    node = make_risk_manager_node(FakeRepository())
    result = await node(_state("AAPL", "HOLD", 0))

    assert result["risk_verdict"].opinion == "APPROVE"
    assert result["final_action"] == "HOLD"
    assert result["final_quantity"] == 0


@pytest.mark.asyncio
async def test_buy_within_limit_is_approved_unchanged():
    node = make_risk_manager_node(FakeRepository())  # no existing position
    result = await node(_state("AAPL", "BUY", 5))

    assert result["risk_verdict"].opinion == "APPROVE"
    assert result["final_action"] == "BUY"
    assert result["final_quantity"] == 5


@pytest.mark.asyncio
async def test_buy_over_limit_is_scaled_down():
    repo = FakeRepository([make_position("AAPL", quantity=MAX_POSITION_QTY - 3)])
    node = make_risk_manager_node(repo)
    result = await node(_state("AAPL", "BUY", 10))

    assert result["risk_verdict"].opinion == "SCALE"
    assert result["risk_verdict"].adjusted_quantity == 3
    assert result["final_action"] == "BUY"
    assert result["final_quantity"] == 3


@pytest.mark.asyncio
async def test_buy_at_cap_is_vetoed():
    repo = FakeRepository([make_position("AAPL", quantity=MAX_POSITION_QTY)])
    node = make_risk_manager_node(repo)
    result = await node(_state("AAPL", "BUY", 1))

    assert result["risk_verdict"].opinion == "VETO"
    assert result["final_action"] == "HOLD"
    assert result["final_quantity"] == 0


@pytest.mark.asyncio
async def test_sell_more_than_held_is_scaled_to_full_close():
    repo = FakeRepository([make_position("AAPL", quantity=4)])
    node = make_risk_manager_node(repo)
    result = await node(_state("AAPL", "SELL", 10))

    assert result["risk_verdict"].opinion == "SCALE"
    assert result["risk_verdict"].adjusted_quantity == 4
    assert result["final_action"] == "SELL"
    assert result["final_quantity"] == 4


@pytest.mark.asyncio
async def test_sell_with_no_position_is_vetoed():
    node = make_risk_manager_node(FakeRepository())
    result = await node(_state("AAPL", "SELL", 5))

    assert result["risk_verdict"].opinion == "VETO"
    assert result["final_action"] == "HOLD"
    assert result["final_quantity"] == 0


@pytest.mark.asyncio
async def test_sell_within_held_is_approved_unchanged():
    repo = FakeRepository([make_position("AAPL", quantity=10)])
    node = make_risk_manager_node(repo)
    result = await node(_state("AAPL", "SELL", 6))

    assert result["risk_verdict"].opinion == "APPROVE"
    assert result["final_action"] == "SELL"
    assert result["final_quantity"] == 6
