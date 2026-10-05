"""
Which LLM each node uses: one place, chosen by environment, recorded per run.

V1 ran on OpenAI (gpt-4o for the Portfolio Manager, gpt-4o-mini for the
Sentiment Analyst). From V2 the project can run on Anthropic's Claude
instead (engineering-log, phase 07). Nodes never construct a client
themselves any more: they call `default_llm(role)` and `structured(...)`,
so the provider is a configuration choice, not a code change.

Environment (all optional):
    LLM_PROVIDER               "openai" (default) or "anthropic"
    PORTFOLIO_MODEL            overrides the provider's default for the PM
    SENTIMENT_MODEL            overrides the provider's default for sentiment

Two Claude API details this module exists to get right:

- Structured output goes through `method="json_schema"` (the API's native
  `output_config.format`), not the default forced tool call: Claude Opus
  5.5 and Sonnet 5.5 reject forced `tool_choice` with a 400.
- `temperature` is only sent where the model accepts it. V1 ran at 0; on
  Claude Opus 5.5 / Sonnet 5.5 / Fable sampling parameters are rejected,
  so those run at the model default and are not bit-reproducible run to
  run. Each run records what it used (`describe()`), so that's visible.
"""

from __future__ import annotations

import os
from typing import Any, Literal

Role = Literal["portfolio_manager", "sentiment_analyst"]

DEFAULT_MODELS: dict[str, dict[Role, str]] = {
    "openai": {"portfolio_manager": "gpt-4o", "sentiment_analyst": "gpt-4o-mini"},
    # Chosen 2026-10-05: the same large/small split V1 had (gpt-4o /
    # gpt-4o-mini), at ~$1.60 per 272-day run (engineering-log, phase 07).
    "anthropic": {
        "portfolio_manager": "claude-sonnet-5-5",
        "sentiment_analyst": "claude-haiku-4-5",
    },
}

# Claude models that still accept `temperature`. Others: model default only.
CLAUDE_TEMPERATURE_OK = ("claude-haiku-4-5",)

# Claude Sonnet 5.5 thinks by default and rejects {"type": "disabled"};
# "between_tools" is its thinking-off setting. Off, to match V1's
# no-reasoning-model setup and keep cost and output comparable. Haiku 4.5
# doesn't think unless asked, so it needs nothing.
CLAUDE_THINKING_OFF = {"claude-sonnet-5-5": {"type": "between_tools"}}


def provider() -> str:
    name = os.environ.get("LLM_PROVIDER", "openai").strip().lower()
    if name not in DEFAULT_MODELS:
        raise ValueError(f"LLM_PROVIDER must be one of {sorted(DEFAULT_MODELS)}, got {name!r}")
    return name


def model_name(role: Role) -> str:
    env_key = "PORTFOLIO_MODEL" if role == "portfolio_manager" else "SENTIMENT_MODEL"
    return os.environ.get(env_key) or DEFAULT_MODELS[provider()][role]


def _claude_temperature(model: str) -> float | None:
    return 0.0 if model.startswith(CLAUDE_TEMPERATURE_OK) else None


def default_llm(role: Role) -> Any:
    """A chat model for `role` from the configured provider."""
    model = model_name(role)
    if provider() == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs: dict[str, Any] = {"model": model, "max_tokens": 4096}
        temperature = _claude_temperature(model)
        if temperature is not None:
            kwargs["temperature"] = temperature
        if model in CLAUDE_THINKING_OFF:
            kwargs["thinking"] = CLAUDE_THINKING_OFF[model]
        return ChatAnthropic(**kwargs)

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=model, temperature=0)


def structured(model: Any, schema: type) -> Any:
    """`model.with_structured_output(schema)`, using native structured
    outputs on Claude. Test fakes only implement the one-argument form."""
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError:  # provider not installed: can't be a Claude model
        ChatAnthropic = None
    if ChatAnthropic is not None and isinstance(model, ChatAnthropic):
        return model.with_structured_output(schema, method="json_schema")
    return model.with_structured_output(schema)


def describe() -> dict:
    """What the next run will use, for backtests.config."""
    name = provider()
    out: dict = {"provider": name}
    for role in ("portfolio_manager", "sentiment_analyst"):
        model = model_name(role)
        if name == "anthropic":
            out[role] = {
                "model": model,
                "temperature": _claude_temperature(model),
                "thinking": CLAUDE_THINKING_OFF.get(model, {}).get("type", "model default"),
            }
        else:
            out[role] = {"model": model, "temperature": 0.0}
    return out
