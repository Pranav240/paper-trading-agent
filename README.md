# Paper Trading Agent

A paper-trading decision-support agent, built in phases to learn backend
API development, real database work, LLM fine-tuning, agentic
orchestration, containerized deployment, and cloud deployment — end to
end, on one real system rather than six disconnected exercises.

**Paper trading only.** This project never touches real capital or a live
brokerage account. The goal is an honestly evaluated system, not a claim
of profitability — if backtesting shows no edge, that is the correct,
reportable outcome.

## Status

Phase 01 (Control API) — in progress.

## Roadmap

**V1 — daily decision-support agent**

1. Control API — FastAPI endpoints for positions/decisions/trigger-run *(this phase)*
2. State & history — Postgres schema (watchlist, price snapshots, decisions, outcomes)
3. Decision agent — LangGraph **multi-agent** flow (supervisor pattern):
   Technical Analyst + Sentiment Analyst report to a Portfolio Manager,
   with a Risk Manager able to veto/scale any trade → paper-trade → log
4. Sentiment model — LoRA fine-tune on financial headlines (Hugging Face PEFT)
5. Build pipeline — Dockerfile + GitHub Actions
6. Scheduled run — deployed on AWS (EC2 + RDS), triggered once per session

**Gate:** V1 must be deployed and running end-to-end on paper, and the
strategy logic must be honestly backtested (realistic slippage/commissions,
walk-forward validation), before V2 begins.

**V2 — continuous intraday extension** (each block upgrades a specific V1 block)

7. Live feed — WebSocket market data (e.g. Alpaca paper trading), replacing the daily fetch
8. Hot state — Redis + a queue decoupling the feed from the decision loop
9. Continuous loop — the same agent logic as a long-running worker reacting per bar
10. Risk guardrails — max position size, daily loss limit, stale-data circuit breaker
11. Ops watch — crash/staleness alerting
12. Session scheduler — market-open/close aware, skips weekends/holidays

## Design decisions log

- **Phase 03 is multi-agent, not a single pipeline.** Technical Analyst and
  Sentiment Analyst each form an independent opinion; a Risk Manager can
  veto or scale down a trade regardless of their views; a Portfolio
  Manager makes the final call by arbitrating between them. Chosen over a
  single-agent pipeline specifically to demonstrate real agentic
  orchestration (LangGraph supervisor pattern), not just chained function
  calls.
  - **Cost control:** not every node needs a frontier model. Technical
    Analyst and Risk Manager are closer to rule-based checks — cheap/local
    models or plain code are fine there. Reserve GPT-4o/Gemini for the
    Portfolio Manager, where judgment under conflicting signals actually
    happens.
  - **Schema consequence for Phase 02:** the decisions log can't be one
    row per cycle anymore. Need a `decisions` table (final calls) plus an
    `agent_opinions` table (each specialist's opinion + reasoning per
    cycle), so a decision can be audited: what did each agent say, and why
    did the Portfolio Manager side with one over another.
- **MCP is an optional add-on, not a core phase.** If added, the lowest-cost
  version is wrapping the Control API as an MCP server (so Claude Desktop
  or another MCP client can query positions/decisions directly) — after
  Phase 01/02 are solid, not instead of them. Not required for the
  multi-agent redesign above; LangGraph's supervisor pattern doesn't need
  MCP to work.

## Phase 01 — Control API

FastAPI service with four endpoints, backed by an in-memory stub store
(no database yet — that's Phase 02). The stub store exists behind a
`get_store()` dependency so Phase 02 only has to change `app/store.py`,
not the route handlers.

### Endpoints

- `GET /health` — liveness check
- `GET /positions` — open paper positions (optional `?symbol=`)
- `GET /decisions` — decision log, most recent first (optional `?symbol=`, `?limit=`)
- `POST /run/trigger` — run one (stubbed) agent decision cycle

### Run it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000/docs for the interactive API docs.

### Test it

```bash
pytest -q
```
