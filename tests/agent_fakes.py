"""
Shared test doubles for the Phase 03 agent nodes.

These exist so every agent-node test can run with no network access at
all — no Alpaca keys, no OpenAI key, no Postgres. Each fake implements
just enough of the relevant Protocol's shape to satisfy the node under
test; that's the entire point of using Protocols (app/agent/data_sources.py,
app/repository/base.py) instead of concrete classes as the node
dependencies.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.models import Decision, Position, RunResult


class FakePriceSource:
    """Returns whatever bars you hand it, ignoring symbol/as_of/lookback —
    the node under test doesn't care where bars come from, just what's in
    them."""

    def __init__(self, bars):
        self._bars = bars

    async def get_recent_bars(self, symbol, as_of, lookback_days=30):
        return self._bars


class FakeHeadlineSource:
    def __init__(self, headlines):
        self._headlines = headlines

    async def get_recent_headlines(self, symbol, as_of, lookback_days=3):
        return self._headlines


class FakeRepository:
    """Only list_positions is exercised by risk_manager today, but all
    three Protocol methods are implemented so this can stand in anywhere
    a Repository is expected."""

    def __init__(self, positions: list[Position] | None = None):
        self._positions = positions or []

    async def list_positions(self, symbol: str | None = None) -> list[Position]:
        if symbol is None:
            return list(self._positions)
        return [p for p in self._positions if p.symbol == symbol]

    async def list_decisions(
        self, symbol: str | None = None, limit: int = 50
    ) -> list[Decision]:
        return []

    async def trigger_run(self) -> RunResult:
        raise NotImplementedError("not needed by these tests")


def make_position(
    symbol: str,
    quantity: int,
    avg_entry_price: str = "100.00",
    current_price: str = "100.00",
) -> Position:
    return Position(
        symbol=symbol,
        quantity=quantity,
        avg_entry_price=Decimal(avg_entry_price),
        current_price=Decimal(current_price),
        unrealized_pnl=Decimal("0"),
        opened_at=datetime(2026, 1, 1),
    )


class FakeStructuredModel:
    """Stands in for `ChatOpenAI(...).with_structured_output(Schema)`.
    `.ainvoke(messages)` just returns whatever Pydantic instance the test
    configured — no API call, no network."""

    def __init__(self, response):
        self._response = response

    async def ainvoke(self, messages):
        return self._response


class FakeLLM:
    """Stands in for `ChatOpenAI` itself. `.with_structured_output(...)`
    ignores the schema argument and returns a FakeStructuredModel that
    always answers with the response given at construction time."""

    def __init__(self, response):
        self._response = response

    def with_structured_output(self, schema):
        return FakeStructuredModel(self._response)


# --------------------------------------------------------------------------
# Phase 10: a fake that IS a LangChain chat model, so callbacks fire exactly
# as they do for a real one (the trace recorder captures its prompt,
# response and token counts). Structured output parses its JSON reply.
# --------------------------------------------------------------------------

import re
from typing import Any, Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda


class JsonFakeChatModel(BaseChatModel):
    """`respond(messages) -> pydantic object`; the reply is its JSON."""

    respond: Any
    model_name: str = "fake-json"

    @property
    def _llm_type(self) -> str:
        return "fake-json"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        content = self.respond(messages).model_dump_json()
        prompt_chars = sum(len(str(m.content)) for m in messages)
        message = AIMessage(
            content=content,
            usage_metadata={"input_tokens": prompt_chars // 4, "output_tokens": len(content) // 4,
                            "total_tokens": prompt_chars // 4 + len(content) // 4},
            response_metadata={"model_name": self.model_name},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def with_structured_output(self, schema, **kwargs):
        return self | RunnableLambda(lambda m: schema.model_validate_json(m.content))


def fixed_json_model(obj, model_name: str = "fake-json") -> JsonFakeChatModel:
    return JsonFakeChatModel(respond=lambda messages: obj, model_name=model_name)


def grounded_explainer_model() -> JsonFakeChatModel:
    """Fake explainer that cites the first headline shown for each driver
    day (or nothing when a day shows none), so its explanations pass the
    citation check the way a well-behaved model's would."""
    from app.agent.explainer import DriverExplanation, ExplanationCall

    def respond(messages):
        text = str(messages[-1].content)
        drivers = []
        for block in re.split(r"\n\n(?=\d{4}-\d{2}-\d{2} \()", text):
            m = re.match(r"(\d{4}-\d{2}-\d{2}) \(", block)
            if not m:
                continue
            ids = [int(i) for i in re.findall(r"^\s+\[(\d+)\]", block, flags=re.M)]
            drivers.append(DriverExplanation(
                date=m.group(1),
                cause="dry run" if ids else "no headline on record",
                headline_ids=ids[:1],
            ))
        return ExplanationCall(summary="dry run", drivers=drivers)

    return JsonFakeChatModel(respond=respond, model_name="fake-explainer")
