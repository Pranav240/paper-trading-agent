"""
Provider selection (app/agent/llm.py). No network: these build clients
and inspect the request they WOULD send, which is where the Claude API
differences bite (forced tool calls and sampling params return 400s on
the newest models).
"""

import pytest

from app.agent.llm import default_llm, describe, structured
from app.agent.state import TentativeDecision
from tests.agent_fakes import FakeLLM

MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


@pytest.fixture
def anthropic_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    monkeypatch.delenv("PORTFOLIO_MODEL", raising=False)
    monkeypatch.delenv("SENTIMENT_MODEL", raising=False)


def _payload(model):
    """The Messages API request a structured call would make."""
    binding = structured(model, TentativeDecision).first
    return model._get_request_payload(MESSAGES, **binding.kwargs)


def test_default_provider_is_openai(monkeypatch):
    for key in ("LLM_PROVIDER", "PORTFOLIO_MODEL", "SENTIMENT_MODEL"):
        monkeypatch.delenv(key, raising=False)
    assert describe() == {
        "provider": "openai",
        "portfolio_manager": {"model": "gpt-4o", "temperature": 0.0},
        "sentiment_analyst": {"model": "gpt-4o-mini", "temperature": 0.0},
    }


def test_unknown_provider_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nope")
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        describe()


def _temperature(payload):
    # anthropic 1.x takes sampling params via extra_body; langchain moves them.
    return payload.get("temperature", payload.get("extra_body", {}).get("temperature"))


def test_claude_defaults_are_the_chosen_models(anthropic_env):
    assert describe() == {
        "provider": "anthropic",
        "portfolio_manager": {
            "model": "claude-sonnet-5-5", "temperature": None, "thinking": "between_tools",
        },
        "sentiment_analyst": {
            "model": "claude-haiku-4-5", "temperature": 0.0, "thinking": "model default",
        },
    }


def test_claude_uses_native_structured_output_not_forced_tool(anthropic_env):
    payload = _payload(default_llm("portfolio_manager"))
    assert payload["model"] == "claude-sonnet-5-5"
    assert payload["output_config"]["format"]["type"] == "json_schema"
    assert "tool_choice" not in payload and "tools" not in payload


def test_sonnet_thinking_off_and_no_temperature(anthropic_env):
    payload = _payload(default_llm("portfolio_manager"))
    # Sonnet 5.5 rejects {"type": "disabled"}; between_tools is its off switch,
    # and it accepts no other thinking field alongside it.
    assert payload["thinking"] == {"type": "between_tools"}
    assert _temperature(payload) is None  # non-default values are rejected


def test_haiku_keeps_temperature_zero(anthropic_env):
    payload = _payload(default_llm("sentiment_analyst"))
    assert payload["model"] == "claude-haiku-4-5"
    assert _temperature(payload) == 0.0  # V1's setting, where allowed
    assert "thinking" not in payload


def test_model_override_from_env(anthropic_env, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_MODEL", "claude-opus-5-5")
    payload = _payload(default_llm("portfolio_manager"))
    assert payload["model"] == "claude-opus-5-5"
    assert _temperature(payload) is None and "thinking" not in payload


def test_structured_leaves_fakes_alone():
    fake = FakeLLM(TentativeDecision(action="HOLD", quantity=0, confidence=0.5, reasoning="t"))
    assert structured(fake, TentativeDecision) is not None
