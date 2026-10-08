# MCP server — plan (add-on, not a roadmap phase)

Written 2026-10-08, before any code. The engineering log listed MCP as an
optional add-on ("wrapping the Control API as an MCP server ... after
Phase 01/02 are solid"); this is that add-on, scoped to what V2 now stores.

## Goal

Let an MCP client (Claude Desktop, Claude Code) answer questions about the
project's recorded decisions from the database itself — "what did the
system do on 2023-09-21 and why?", "why was this trade cut?", "show the
trace" — instead of someone running `scripts/audit_decision.py` by hand.

**Read-only, by construction.** No tool writes, and none triggers a run:
a run spends API money, and an LLM client should not be able to start one.

## Design

- `app/mcp_server.py`, the official `mcp` Python SDK (`FastMCP`), **stdio**
  transport: the client starts it as a local process; no port is opened.
- Hand-written SQL through `psycopg`, as everywhere else; reuses
  `app/agent/trace_audit.py` (`load_steps`, `format_audit`, `completeness`)
  rather than a second copy of the trace logic.
- **Read-only enforced in the database, not just by convention:** every
  connection runs with `default_transaction_read_only = on`, so a write
  fails in Postgres even if a tool had a bug.
- Every list is capped (default 20, max 200 rows). Prompts and replies in
  traces are shortened unless `full=True`, so one call cannot flood the
  client's context.
- Errors are plain sentences ("no decision 123", "decision 45 has no
  trace — made before phase 10"), not stack traces.

## Tools

| Tool | Returns | Source |
|---|---|---|
| `list_backtests()` | id, name, window, status, models, git commit | `backtests` |
| `list_decisions(backtest_id?, symbol?, action?, limit)` | id, date, symbol, action, confidence, whether VaR changed it | `decisions`, `runs`, `var_forecasts` |
| `get_decision(decision_id)` | the decision, each agent's opinion and reasoning, the VaR forecast and the quantity it allowed | `decisions`, `agent_opinions`, `var_forecasts` |
| `explain_decision(decision_id)` | the stored explanation: summary, cited headlines, whether the citations checked out | `risk_explanations` |
| `get_trace(decision_id, full=False)` | the step-by-step trace, plus the completeness check | `trace_steps` via `trace_audit` |

Five tools, all answering from stored rows. Nothing is computed that the
project has not already computed and stored, so the server cannot
produce a number the record does not contain.

## Tests (pytest, live Postgres, as CI already runs)

1. The server lists exactly these five tools, and every tool's schema matches.
2. Each tool returns the expected fields on seeded rows (in-memory MCP
   client from the SDK, no subprocess).
3. **A write through the server's connection fails** (read-only proof).
4. Limits: `limit=10_000` comes back capped at 200.
5. Unknown ids give the plain-sentence error, not an exception.
6. `get_trace` output equals `scripts/audit_decision.py` output for the
   same decision (no second, drifting implementation).

## Done means

- Tests above pass in CI; the existing 124 still pass.
- **A real check in Claude Desktop**, logged in the engineering log: three
  questions about backtest 136 (a VaR-changed decision, its explanation,
  its trace), each answer compared against `audit_decision.py` for the same
  decision. Any wrong or invented detail is reported, not fixed silently.
- README: one section with the Claude Desktop config snippet.

## Cost and risk

- **$0 in API spend.** The MCP client does the reasoning on the owner's
  Claude app; the server only reads Postgres.
- New dependency: `mcp` (pinned in `requirements.txt`).
- Needs the `pta-postgres` container running when the client is used.
- Out of scope: write tools, triggering runs, a remote (HTTP) server,
  authentication. A remote server would need all of these first.
