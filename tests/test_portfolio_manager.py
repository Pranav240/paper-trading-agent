import pytest

from app.agent.portfolio_manager import make_portfolio_manager_node
from app.agent.state import AgentOpinion, TentativeDecision
from tests.agent_fakes import FakeLLM


@pytest.mark.asyncio
async def test_synthesizes_both_opinions_into_tentative_decision_and_logs_it():
    technical = AgentOpinion(
        agent_name="technical_analyst",
        opinion="BUY",
        confidence=0.65,
        reasoning="RSI oversold, price above SMA-20.",
        raw_output={"rsi_14": 25.0},
    )
    sentiment = AgentOpinion(
        agent_name="sentiment_analyst",
        opinion="BUY",
        confidence=0.6,
        reasoning="Headlines lean positive.",
        raw_output={"n_headlines": 3},
    )
    fake_decision = TentativeDecision(
        action="BUY", quantity=10, confidence=0.7, reasoning="Both specialists agree."
    )
    node = make_portfolio_manager_node(llm=FakeLLM(fake_decision))

    result = await node(
        {
            "symbol": "AAPL",
            "technical_opinion": technical,
            "sentiment_opinion": sentiment,
        }
    )

    assert result["tentative_decision"] == fake_decision

    logged = result["portfolio_opinion"]
    assert logged.agent_name == "portfolio_manager"
    assert logged.opinion == "BUY"
    assert logged.confidence == 0.7
    assert logged.raw_output["quantity"] == 10
    assert logged.raw_output["technical_opinion"] == "BUY"
    assert logged.raw_output["sentiment_opinion"] == "BUY"
