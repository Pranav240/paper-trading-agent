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

Phase 03 (Decision agent) — built and unit-tested against fakes (no live
API calls yet; waiting on OpenAI + Alpaca API keys before the first real
run). Phase 02 (State & history) and Phase 01 (Control API) — done.

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
- **Sentiment Analyst uses GPT-4o-mini via API, not a locally fine-tuned
  FinBERT, for now.** The original plan (and still the Phase 04 plan) is
  a LoRA-fine-tuned local model. The dev sandbox this project is being
  built in blocks `huggingface.co` at the network level, so model weights
  can't be downloaded there — this is an environment limitation, not a
  design change. `app/agent/sentiment_analyst.py` is written against the
  same `AgentOpinion` contract either way, so swapping in a local model
  later only touches that one file.
- **US markets (Alpaca) only, for now — Indian markets considered and
  deliberately deferred.** Checked what an NSE/BSE version would need:
  Zerodha/Upstox/Angel One/Fyers all offer free order-execution APIs, but
  none offer a paper-trading sandbox, and there's no free equivalent to
  Alpaca's News API or to the FNSPID historical-headline dataset for
  Indian equities — the Sentiment Analyst and the backtest data plan
  would both need new, currently-unresearched sourcing. Since this
  project's own paper trading is simulated in Postgres (not through a
  broker's paper-money account), the `PriceDataSource`/`HeadlineSource`
  protocols in `app/agent/data_sources.py` make an Indian data source a
  future swap-in, not a rewrite — worth revisiting as a V3 extension,
  not a Phase 03 blocker.

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

## Phase 02 — State & history (Postgres)

Real schema, no ORM: tables are hand-written DDL in `db/migrations/*.sql`,
applied by a ~60-line migration runner (`db/migrate.py`) that just tracks
which files have already run — no autogeneration, no model-to-schema
diffing. Queries in `app/repository/postgres.py` are raw parameterized
SQL via `psycopg` (async), not a query builder.

### Schema

- `watchlist` — symbols being tracked
- `price_snapshots` — OHLCV market data per symbol per timestamp
- `runs` — one row per agent decision cycle
- `decisions` — the final call per symbol per run (action, confidence,
  reasoning, technicals/sentiment snapshots as JSONB)
- `agent_opinions` — one row per specialist agent's opinion per decision
  (added for the Phase 03 multi-agent design — see decisions log above);
  FK to `decisions`
- `outcomes` — how a decision actually performed (entry/exit price,
  realized P&L); FK to `decisions`
- `positions` — a VIEW (not a table) computed by joining open `outcomes`
  against the latest `price_snapshots` row per symbol. Deliberately not
  stored, so it can never drift out of sync with the data it's derived
  from.
- `backtests` — one row per backtest execution (name, date window,
  config); `runs.mode` (`LIVE`/`BACKTEST`) and `runs.backtest_id` tie a
  decision cycle to a specific backtest, enforced by a CHECK constraint
  so the two can never point at each other inconsistently.
  `runs.as_of` is the date a run's decision is *for*, separate from
  `started_at` (when it actually executed) — for a live run they're the
  same moment; for a backtest run, `as_of` is the simulated historical
  date while `started_at` is whenever the backtest was actually run.
  Added ahead of Phase 03 specifically so backtesting doesn't require a
  schema migration later — see `docs/backtesting-plan.md`.

### The seam paying off

Phase 01's `get_store()` FastAPI dependency now returns a
`PostgresRepository` instead of an `InMemoryStore` — both implement the
same `Repository` protocol (`app/repository/base.py`), so none of
`app/routers/*.py` changed except becoming `async def` (real I/O now
happens, so routes await it). `InMemoryStore` still exists, used only by
the test suite for fast, database-independent tests.

### Run it

Requires a local Postgres reachable via `DATABASE_URL` (defaults to
`postgresql://pta:pta_dev_password@127.0.0.1:5432/paper_trading_agent`).

```bash
python db/migrate.py                              # apply schema
psql "$DATABASE_URL" -f db/seed.sql                # seed at least one watchlist symbol
uvicorn app.main:app --reload
```

(`db/seed.sql` isn't a migration — `decisions` and `outcomes` have a
foreign key into `watchlist`, so `POST /run/trigger` fails with a foreign
key violation until at least one symbol exists there.)

Restart the server and hit `/decisions` again — the data is still there.
That's the actual proof this phase solved Phase 01's core limitation.

### Test it

```bash
pytest -q   # runs with SKIP_DB_STARTUP=1 — no Postgres needed
```

## Phase 03 — Decision agent (LangGraph multi-agent flow)

`POST /run/trigger` now runs a real LangGraph flow per active watchlist
symbol instead of writing stubbed rows. Shape (fan-out, then fan-in, then
a gate):

```
START --> technical_analyst --\
                                +--> portfolio_manager --> risk_manager --> END
START --> sentiment_analyst --/
```

### The four nodes

- **Technical Analyst** (`app/agent/technical_analyst.py`) — rule-based,
  no LLM call. Pulls ~40 days of daily bars, computes RSI-14/SMA-20
  (`app/agent/indicators.py`, plain Python, no ta-lib), applies a small
  explainable if/elif chain. RSI/SMA are numbers, not judgment calls —
  paying for an LLM to reason about arithmetic isn't worth it.
- **Sentiment Analyst** (`app/agent/sentiment_analyst.py`) — LLM-backed
  (GPT-4o-mini via `langchain-openai`'s `with_structured_output`), reads
  up to 3 days of headlines and judges tone. If there are zero headlines,
  it returns HOLD **without calling the LLM at all** — cost control:
  nothing to read means nothing to pay for.
- **Portfolio Manager** (`app/agent/portfolio_manager.py`) — LLM-backed
  (GPT-4o), synthesizes both specialists' opinions (weighing reasoning
  and confidence, not just labels) into a `TentativeDecision`. This is
  the one node that's genuinely a judgment call, so it gets the frontier
  model.
- **Risk Manager** (`app/agent/risk_manager.py`) — rule-based, no LLM.
  Reads the current position via `repository.list_positions()` and
  enforces a hard position-size cap (`MAX_POSITION_QTY`, currently 20
  shares) plus "can't sell more than you hold" — approving, vetoing, or
  scaling the tentative decision. This node has final authority:
  `final_action`/`final_quantity` (what actually gets paper-traded) are
  set here, not by the Portfolio Manager.

Every node factory takes its dependencies (`PriceDataSource`,
`HeadlineSource`, `Repository`, an optional LLM) as arguments rather than
constructing them internally — same seam pattern as Phase 01/02's
`Repository` protocol, applied to market data
(`app/agent/data_sources.py`) and now to the agent nodes themselves. It's
what makes every node (and the full graph, `app/agent/graph.py`)
unit-testable with fakes and no network access at all — see
`tests/agent_fakes.py` and `tests/test_*.py` for `risk_manager`,
`technical_analyst`, `sentiment_analyst`, `portfolio_manager`, and a
full `test_graph.py` pipeline test with everything faked.

### Orchestration and persistence (`app/agent/runner.py`)

The graph itself handles exactly one symbol per call. `runner.py` is one
level up: it loops over every active `watchlist` symbol, and — inside a
single Postgres transaction, same all-or-nothing guarantee Phase 02's
stub already had — writes one `runs` row, one `decisions` row and four
`agent_opinions` rows per symbol, then executes the paper trade against
`outcomes`:

- **BUY** always opens a new lot (its own entry price, its own
  `opened_at`) rather than merging into an existing open position for
  that symbol — that's what keeps the positions view's quantity-weighted
  average entry price correct.
- **SELL** closes open lots oldest-first (FIFO) until the sold quantity
  is accounted for; a partial-lot sale shrinks the existing open row and
  inserts a new `CLOSED` row for the sold portion.
- If a SELL ever exceeds what's actually held, that's treated as a bug
  in `risk_manager` (which should always have caught it first) and
  raises loudly, rather than silently modeling a short position — this
  system is long-only by design.

`PostgresRepository.trigger_run()` is now a thin wrapper: build the live
data sources, build the graph, call `run_decision_cycle()`. Keeping the
actual logic in `graph.py`/`runner.py` rather than inline in the
repository is what makes it testable without a FastAPI app or a live
database.

### Known gap (flagged honestly, not fixed yet)

`runner.py` prices paper trades from the Technical Analyst's own
`current_price` (captured while computing RSI/SMA), **not** from
`price_snapshots` — because nothing in Phase 03 writes to that table
yet. The `positions` view's `current_price`/`unrealized_pnl` will
therefore show `NULL` until something (a natural small addition: write
one row per symbol per run, right after the Technical Analyst node runs)
actually populates `price_snapshots`. Not fixed here to avoid silently
expanding this phase's scope.

### Run it

Requires `OPENAI_API_KEY`, `ALPACA_API_KEY`, and `ALPACA_SECRET_KEY` in
`.env` (see `.env.example`) — not yet live-tested against real API calls
in this environment.

```bash
uvicorn app.main:app --reload
curl -X POST http://127.0.0.1:8000/run/trigger
```

### Test it

```bash
pytest -q   # 30 tests, all against fakes — no API keys or network needed
```
