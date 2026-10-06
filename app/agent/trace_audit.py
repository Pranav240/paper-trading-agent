"""
Reading a decision's trace back (phase 10): what scripts/audit_decision.py
prints, and the pre-registered completeness check -- an explanation's
citation check rebuilt from the trace ALONE must reproduce the stored
risk_explanations row exactly (docs/phase10-plan.md, section 4).
"""

from __future__ import annotations

import json

from psycopg.rows import dict_row

from app.agent.explainer import ExplanationCall, validate


async def load_steps(conn, decision_id: int) -> list[dict]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT step, parent_step, node, kind, input, output, model, input_tokens, "
            "output_tokens, started_at, duration_ms, error FROM trace_steps "
            "WHERE decision_id = %s ORDER BY step",
            (decision_id,),
        )
        return await cur.fetchall()


def rebuild_explanation_check(steps: list[dict]) -> dict | None:
    """The citation check, from trace steps only: retrieved ids from the
    explainer's traced retrievals, drivers from its traced model reply."""
    retrievals = [s for s in steps if s["node"] == "explainer" and s["kind"] == "retrieval"]
    replies = [s for s in steps if s["node"] == "explainer" and s["kind"] == "llm"]
    if not replies:
        return None
    retrieved = {s["input"]["day"]: [r["id"] for r in s["output"]["results"]] for s in retrievals}
    call = ExplanationCall.model_validate_json(replies[-1]["output"]["content"])
    cited, problems = validate(call, retrieved)
    return {"retrieved": retrieved, "cited_headline_ids": cited,
            "validation_problems": problems, "citations_valid": not problems}


async def completeness(conn, decision_ids: list[int] | None = None) -> tuple[int, list[str]]:
    """(explanations checked, mismatches). Every stored explanation whose
    decision has a trace is rebuilt and compared field by field."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT e.decision_id, e.retrieved, e.cited_headline_ids, e.validation_problems, "
            "e.citations_valid FROM risk_explanations e "
            "WHERE EXISTS (SELECT 1 FROM trace_steps t WHERE t.decision_id = e.decision_id) "
            + ("AND e.decision_id = ANY(%s) " if decision_ids else "") + "ORDER BY e.decision_id",
            (decision_ids,) if decision_ids else (),
        )
        stored = await cur.fetchall()
    mismatches = []
    for row in stored:
        rebuilt = rebuild_explanation_check(await load_steps(conn, row["decision_id"]))
        if rebuilt is None:
            mismatches.append(f"decision {row['decision_id']}: no traced explainer reply")
            continue
        for field in ("retrieved", "cited_headline_ids", "validation_problems", "citations_valid"):
            if rebuilt[field] != row[field]:
                mismatches.append(f"decision {row['decision_id']}: {field} rebuilt {rebuilt[field]!r} != stored {row[field]!r}")
    return len(stored), mismatches


def _short(text, width=110) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def format_audit(decision: dict, steps: list[dict], full: bool = False) -> str:
    """Human-readable chain for one decision."""
    lines = [
        f"Decision {decision['id']}: {decision['symbol']} {decision['action']} "
        f"(as of {decision['as_of']}, run {decision['run_id']})",
        f"Reason recorded: {_short(decision['reasoning'], 300)}",
        f"{len(steps)} trace steps", "",
    ]
    for s in steps:
        head = f"[{s['step']}] {s['node']} · {s['kind']}"
        if s["model"]:
            head += f" · {s['model']}"
        if s["duration_ms"] is not None:
            head += f" · {s['duration_ms']:.0f} ms"
        if s["input_tokens"] is not None:
            head += f" · {s['input_tokens']} in / {s['output_tokens']} out tokens"
        lines.append(head)
        if s["error"]:
            lines.append(f"    ERROR: {s['error']}")
        inp, out = s["input"], s["output"]
        if s["kind"] == "llm":
            for m in inp.get("messages", []):
                body = m["content"] if full else _short(m["content"], 160)
                lines.append(f"    {m['role']}: {body}")
            reply = out.get("content", "")
            lines.append("    reply: " + (reply if full else _short(reply, 300)))
        elif s["kind"] == "retrieval":
            where = inp.get("day") or inp.get("as_of")
            lines.append(f"    {inp.get('method', 'lookup')} for {where}: {len(out.get('results', []))} headlines")
            for r in out.get("results", [])[: (None if full else 5)]:
                score = f" score={r['score']:.3f}" if r.get("score") is not None else ""
                ident = f"[{r['id']}] " if "id" in r else ""
                lines.append(f"      {r.get('rank', '-')}. {ident}{_short(r['headline'], 90)}{score}")
        else:
            lines.append("    in:  " + _short(json.dumps(inp, default=str), 300 if not full else 10_000))
            lines.append("    out: " + _short(json.dumps(out, default=str), 300 if not full else 10_000))
    return "\n".join(lines)
