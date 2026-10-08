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


# ---------------------------------------------------------------------------
# V2 VaR budget (phase 07). Bars are built from a known return series so the
# VaR, and the share limit it implies, are known in advance.
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.agent.data_sources import PriceBar
from app.agent.risk_manager import var_max_qty

AS_OF = datetime(2024, 1, 2, 12, tzinfo=timezone.utc)


def _bars(symbol: str, returns: list[float]) -> list[PriceBar]:
    closes = [100.0]
    for r in returns:
        closes.append(closes[-1] * (1 + r))
    start = datetime(2023, 1, 2, 5, tzinfo=timezone.utc)
    return [
        PriceBar(
            symbol=symbol,
            timestamp=start + timedelta(days=i),
            open=Decimal(str(c)), high=Decimal(str(c)), low=Decimal(str(c)),
            close=Decimal(str(c)), volume=1,
        )
        for i, c in enumerate(closes)
    ]


class SymbolPriceSource:
    def __init__(self, bars_by_symbol):
        self._bars = bars_by_symbol

    async def get_recent_bars(self, symbol, as_of, lookback_days=30):
        return self._bars[symbol]


# 250 returns, 20 losses of 1%..20%: VaR = 13th largest loss = 8%.
# Budget: floor(20 * 2% / 8%) = 5 shares.
HIGH_VOL = [-0.01 * i for i in range(1, 21)] + [0.0] * 230
# Same shape scaled to 1%: floor(20 * 2% / 1%) = 40, capped at 20.
LOW_VOL = [r / 8 for r in HIGH_VOL]


def _v2_node(positions, bars_by_symbol):
    return make_risk_manager_node(
        FakeRepository(positions), SymbolPriceSource(bars_by_symbol)
    )


def _v2_state(action, quantity):
    return {**_state("AAPL", action, quantity), "as_of": AS_OF}


def test_var_max_qty_known_values():
    assert var_max_qty(0.08) == 5
    assert var_max_qty(0.025) == 16  # 15.999... without the rounding guard
    assert var_max_qty(0.01) == MAX_POSITION_QTY
    assert var_max_qty(-0.001) == MAX_POSITION_QTY


@pytest.mark.asyncio
async def test_high_var_scales_buy_to_budget():
    node = _v2_node([], {"AAPL": _bars("AAPL", HIGH_VOL)})
    result = await node(_v2_state("BUY", 10))

    raw = result["risk_opinion"].raw_output
    assert raw["var_95"] == pytest.approx(0.08)
    assert raw["var_max_qty"] == 5
    assert result["risk_verdict"].opinion == "SCALE"
    assert result["final_action"] == "BUY"
    assert result["final_quantity"] == 5
    assert raw["risk_flags"] == ["var_budget_scale"]


@pytest.mark.asyncio
async def test_budget_tightens_after_v1_scale():
    # V1 scales BUY 20 to 17 (3 held); the budget then allows only 5 - 3 = 2.
    node = _v2_node([make_position("AAPL", 3)], {"AAPL": _bars("AAPL", HIGH_VOL)})
    result = await node(_v2_state("BUY", 20))

    assert result["risk_verdict"].opinion == "SCALE"
    assert result["risk_verdict"].adjusted_quantity == 2
    assert result["final_quantity"] == 2


@pytest.mark.asyncio
async def test_buy_vetoed_when_holding_budget_already():
    node = _v2_node([make_position("AAPL", 5)], {"AAPL": _bars("AAPL", HIGH_VOL)})
    result = await node(_v2_state("BUY", 3))

    assert result["risk_verdict"].opinion == "VETO"
    assert result["final_action"] == "HOLD"
    assert result["final_quantity"] == 0
    assert "var_budget_veto" in result["risk_opinion"].raw_output["risk_flags"]


@pytest.mark.asyncio
async def test_over_budget_position_is_flagged_not_force_sold():
    node = _v2_node([make_position("AAPL", 8)], {"AAPL": _bars("AAPL", HIGH_VOL)})
    result = await node(_v2_state("HOLD", 0))

    assert result["final_action"] == "HOLD"
    assert result["final_quantity"] == 0
    assert result["risk_opinion"].raw_output["risk_flags"] == ["over_var_budget"]


@pytest.mark.asyncio
async def test_sell_is_not_limited_by_budget():
    node = _v2_node([make_position("AAPL", 8)], {"AAPL": _bars("AAPL", HIGH_VOL)})
    result = await node(_v2_state("SELL", 3))

    assert result["risk_verdict"].opinion == "APPROVE"
    assert result["final_action"] == "SELL"
    assert result["final_quantity"] == 3


@pytest.mark.asyncio
async def test_low_var_leaves_v1_decision_unchanged():
    node = _v2_node([], {"AAPL": _bars("AAPL", LOW_VOL)})
    result = await node(_v2_state("BUY", 10))

    raw = result["risk_opinion"].raw_output
    assert raw["var_95"] == pytest.approx(0.01)
    assert raw["var_max_qty"] == MAX_POSITION_QTY
    assert result["risk_verdict"].opinion == "APPROVE"
    assert result["final_quantity"] == 10
    assert raw["risk_flags"] == []


@pytest.mark.asyncio
async def test_short_history_falls_back_to_v1_and_flags():
    node = _v2_node([], {"AAPL": _bars("AAPL", HIGH_VOL[:100])})
    result = await node(_v2_state("BUY", 10))

    raw = result["risk_opinion"].raw_output
    assert raw["var_95"] is None
    assert raw["risk_flags"] == ["var_unavailable"]
    assert result["risk_verdict"].opinion == "APPROVE"
    assert result["final_quantity"] == 10


@pytest.mark.asyncio
async def test_correlation_with_other_holding_is_flagged_only():
    wiggle = [0.01 * ((i * 7) % 5 - 2) for i in range(250)]
    bars = {
        "AAPL": _bars("AAPL", wiggle),
        "MSFT": _bars("MSFT", wiggle),              # correlation +1
        "XOM": _bars("XOM", [-r for r in wiggle]),  # correlation -1
    }
    positions = [make_position("MSFT", 5), make_position("XOM", 5)]
    node = _v2_node(positions, bars)
    result = await node(_v2_state("BUY", 1))

    raw = result["risk_opinion"].raw_output
    assert raw["correlations"]["MSFT"] == pytest.approx(1.0)
    assert raw["correlations"]["XOM"] == pytest.approx(-1.0)
    assert raw["risk_flags"] == ["high_correlation:MSFT"]
    assert result["final_quantity"] == 1


@pytest.mark.asyncio
async def test_var_tail_lists_the_days_behind_the_var():
    node = _v2_node([], {"AAPL": _bars("AAPL", HIGH_VOL)})
    raw = (await node(_v2_state("BUY", 10)))["risk_opinion"].raw_output

    tail = raw["var_tail"]
    # k = 13 worst returns, worst first; HIGH_VOL's losses are -1%..-20% on
    # its first 20 days, so the worst is -20% on the 21st bar's date.
    assert len(tail) == 13
    assert tail[0]["return"] == pytest.approx(-0.20)
    assert tail[0]["date"] == "2023-01-22"
    # The 13th worst loss is the VaR itself.
    assert -tail[-1]["return"] == pytest.approx(raw["var_95"])
