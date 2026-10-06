"""
Reasoning trace (V2, phase 10; docs/phase10-plan.md).

One TraceRecorder per decision collects every step of the graph run in
memory, in arrival order; record_trace() writes them to `trace_steps` in
the same transaction as the decision.

- LLM calls are captured by the recorder acting as a LangChain callback
  (passed in the graph's run config, so every chat model in every node is
  covered with no per-node code): the exact prompt messages, the raw
  response, model, tokens, duration and any error. LangGraph tags each
  call with the node it ran in.
- Retrievals, checks and rules are recorded by the code that performs
  them, through `record()`, which reaches the current decision's recorder
  via a context variable and does nothing when no recorder is active
  (tests, live runs without tracing).

Never stored: API keys or request headers. Invocation parameters are
copied through an allow-list, and record_trace() refuses to write
anything that looks like a key (KEY_PATTERN).
"""

from __future__ import annotations

import json
import re
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage

_current: ContextVar["TraceRecorder | None"] = ContextVar("trace_recorder", default=None)

# Invocation parameters worth keeping; everything else (clients, headers,
# credentials) is dropped.
PARAM_ALLOWLIST = (
    "model", "model_name", "max_tokens", "temperature", "thinking",
    "output_config", "response_format", "effort", "_type",
)
# Anthropic / OpenAI style secrets; a match anywhere blocks the write.
KEY_PATTERN = re.compile(r"sk-(ant-)?[A-Za-z0-9_\-]{16,}")


def _message(m: BaseMessage) -> dict:
    return {"role": m.type, "content": m.content}


class TraceRecorder(BaseCallbackHandler):
    """Collects one decision's trace steps."""

    run_inline = True  # keep steps in true arrival order, same thread

    def __init__(self) -> None:
        super().__init__()
        self.steps: list[dict] = []
        self._open: dict[UUID, tuple[int, float]] = {}

    # -- explicit steps ----------------------------------------------------
    def add(self, *, node: str, kind: str, input: dict, output: dict,
            parent_step: int | None = None, model: str | None = None,
            duration_ms: float | None = None, error: str | None = None,
            input_tokens: int | None = None, output_tokens: int | None = None) -> int:
        self.steps.append({
            "step": len(self.steps), "parent_step": parent_step, "node": node, "kind": kind,
            "input": input, "output": output, "model": model,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "started_at": datetime.now(timezone.utc), "duration_ms": duration_ms, "error": error,
        })
        return len(self.steps) - 1

    # -- LangChain callbacks -------------------------------------------------
    def on_chat_model_start(self, serialized: dict, messages: list[list[BaseMessage]], *,
                            run_id: UUID, metadata: dict | None = None,
                            invocation_params: dict | None = None, **kwargs: Any) -> None:
        params = {k: v for k, v in (invocation_params or kwargs.get("invocation_params") or {}).items()
                  if k in PARAM_ALLOWLIST}
        node = (metadata or {}).get("langgraph_node") or "unknown"
        idx = self.add(
            node=node, kind="llm",
            input={"messages": [_message(m) for m in messages[0]], "params": params},
            output={}, model=params.get("model") or params.get("model_name"),
        )
        self._open[run_id] = (idx, time.perf_counter())

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        idx, t0 = self._open.pop(run_id, (None, None))
        if idx is None:
            return
        step = self.steps[idx]
        step["duration_ms"] = (time.perf_counter() - t0) * 1000
        try:
            message = response.generations[0][0].message
        except (IndexError, AttributeError):
            return
        step["output"] = {"content": message.content,
                          "tool_calls": getattr(message, "tool_calls", None) or []}
        usage = getattr(message, "usage_metadata", None) or {}
        step["input_tokens"] = usage.get("input_tokens")
        step["output_tokens"] = usage.get("output_tokens")
        step["model"] = (message.response_metadata or {}).get("model_name") or step["model"]

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        idx, t0 = self._open.pop(run_id, (None, None))
        if idx is not None:
            self.steps[idx]["error"] = repr(error)
            self.steps[idx]["duration_ms"] = (time.perf_counter() - t0) * 1000

    # -- context -----------------------------------------------------------
    def activate(self):
        """Make this the recorder `record()` writes to; returns a reset token."""
        return _current.set(self)

    @staticmethod
    def deactivate(token) -> None:
        _current.reset(token)


def record(*, node: str, kind: str, input: dict, output: dict, **extra: Any) -> int | None:
    """Add a step to the active recorder, if any. Returns its step index."""
    recorder = _current.get()
    if recorder is None:
        return None
    return recorder.add(node=node, kind=kind, input=input, output=output, **extra)


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


async def record_trace(conn, *, decision_id: int, recorder: TraceRecorder) -> int:
    """Write the recorder's steps for `decision_id`. Same transaction as the
    decision. Refuses (raises) if any step contains something key-like."""
    import psycopg

    rows = []
    for s in recorder.steps:
        inp, out = _jsonable(s["input"]), _jsonable(s["output"])
        if KEY_PATTERN.search(json.dumps(inp)) or KEY_PATTERN.search(json.dumps(out)):
            raise ValueError(f"trace step {s['step']} ({s['node']}) contains a key-like string; not stored")
        rows.append((
            decision_id, s["step"], s["parent_step"], s["node"], s["kind"],
            psycopg.types.json.Json(inp), psycopg.types.json.Json(out), s["model"],
            s["input_tokens"], s["output_tokens"], s["started_at"], s["duration_ms"], s["error"],
        ))
    async with conn.cursor() as cur:
        await cur.executemany(
            """
            INSERT INTO trace_steps
                (decision_id, step, parent_step, node, kind, input, output, model,
                 input_tokens, output_tokens, started_at, duration_ms, error)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
    return len(rows)
