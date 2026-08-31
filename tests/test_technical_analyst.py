"""
Tests the Technical Analyst's RULES, not its indicator math (that's
already covered by test_indicators.py). `rsi`/`sma` are monkeypatched to
return fixed values so each test targets exactly one branch of the
if/elif chain in app/agent/technical_analyst.py, independent of what
input closes would actually produce that RSI/SMA in real math.
"""

from datetime import datetime
from decimal import Decimal

import pytest

import app.agent.technical_analyst as ta_module
from app.agent.technical_analyst import make_technical_analyst_node
from tests.agent_fakes import FakePriceSource


def _bar(close):
    from app.agent.data_sources import PriceBar

    return PriceBar(
        symbol="AAPL",
        timestamp=datetime(2026, 1, 1),
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=1000,
    )


@pytest.mark.asyncio
async def test_insufficient_history_returns_hold_without_opinion_guessing(monkeypatch):
    monkeypatch.setattr(ta_module, "rsi", lambda closes, period=14: None)
    monkeypatch.setattr(ta_module, "sma", lambda closes, period: None)

    node = make_technical_analyst_node(FakePriceSource([_bar("100")]))
    result = await node({"symbol": "AAPL", "as_of": datetime(2026, 1, 1)})

    opinion = result["technical_opinion"]
    assert opinion.opinion == "HOLD"
    assert opinion.confidence is None
    assert "not enough" in opinion.reasoning


@pytest.mark.asyncio
async def test_oversold_and_above_sma_is_buy(monkeypatch):
    monkeypatch.setattr(ta_module, "rsi", lambda closes, period=14: Decimal("25"))
    monkeypatch.setattr(ta_module, "sma", lambda closes, period: Decimal("95"))

    node = make_technical_analyst_node(FakePriceSource([_bar("100")]))
    result = await node({"symbol": "AAPL", "as_of": datetime(2026, 1, 1)})

    opinion = result["technical_opinion"]
    assert opinion.opinion == "BUY"
    assert opinion.confidence == 0.65
    assert opinion.raw_output["rsi_14"] == 25.0


@pytest.mark.asyncio
async def test_overbought_is_sell_even_if_above_sma(monkeypatch):
    monkeypatch.setattr(ta_module, "rsi", lambda closes, period=14: Decimal("75"))
    monkeypatch.setattr(ta_module, "sma", lambda closes, period: Decimal("90"))

    node = make_technical_analyst_node(FakePriceSource([_bar("100")]))
    result = await node({"symbol": "AAPL", "as_of": datetime(2026, 1, 1)})

    opinion = result["technical_opinion"]
    assert opinion.opinion == "SELL"
    assert opinion.confidence == 0.60


@pytest.mark.asyncio
async def test_neutral_rsi_above_sma_is_mild_buy(monkeypatch):
    monkeypatch.setattr(ta_module, "rsi", lambda closes, period=14: Decimal("50"))
    monkeypatch.setattr(ta_module, "sma", lambda closes, period: Decimal("90"))

    node = make_technical_analyst_node(FakePriceSource([_bar("100")]))
    result = await node({"symbol": "AAPL", "as_of": datetime(2026, 1, 1)})

    opinion = result["technical_opinion"]
    assert opinion.opinion == "BUY"
    assert opinion.confidence == 0.55


@pytest.mark.asyncio
async def test_neutral_rsi_at_or_below_sma_is_hold(monkeypatch):
    monkeypatch.setattr(ta_module, "rsi", lambda closes, period=14: Decimal("50"))
    monkeypatch.setattr(ta_module, "sma", lambda closes, period: Decimal("110"))

    node = make_technical_analyst_node(FakePriceSource([_bar("100")]))
    result = await node({"symbol": "AAPL", "as_of": datetime(2026, 1, 1)})

    opinion = result["technical_opinion"]
    assert opinion.opinion == "HOLD"
    assert opinion.confidence == 0.50
