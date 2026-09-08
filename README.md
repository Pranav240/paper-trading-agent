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

Phase 03 (Decision agent) — done, including a full live end-to-end run
against a real Postgres database (real Alpaca market data + news, real
OpenAI reasoning from both LLM nodes, a correctly tracked real paper
position). Phase 02 (State & history) and Phase 01 (Control API) — done.

Backtesting infrastructure (the gate before Phase 04/V2, per the roadmap
below) — schema, historical headline source, backtest-scoped repository,
and the backtest runner itself are built and integration-tested; see
[Backtesting infrastructure](#backtesting-infrastructure). Not yet run
against real data — the FNSPID historical-headlines dataset hasn't been
imported yet, so there are no actual backtest *results* to report.

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
- **Ablate a component before investing in improving it.** Phase 04 was
  about to spend ~6,000 labelling calls collecting training data for the
  Sentiment Analyst. Ablating the node first — `run_backtest.py
  --no-sentiment` replaces its content with a fixed neutral stand-in
  while leaving the graph structure and the Portfolio Manager's real LLM
  calls untouched — cost four backtest runs and under a dollar, and
  showed the node was a net *negative*: mark-to-market P&L over a
  13-month AAPL window was ~60 better without it (with-node runs +864.62
  / +840.45, without-node +896.15 / +929.84, buy & hold +903.80). Running
  two replicates per configuration in the same exercise is what made that
  readable — it measured the run-to-run noise floor (24.2 and 33.7)
  instead of assuming one. With n=2 per group this is directionally
  clear, not statistically strong; what it does rule out is "the gap is
  pure noise."
- **An agent that abstains most of the time is not automatically
  harmless.** The categorical Sentiment Analyst said HOLD on 96.8% of 557
  calls, which is exactly why it looked safe to keep. The action counts
  from the ablation show what it was really doing: removing it produced
  ~9 more BUYs, ~6 more SELLs and ~18 fewer HOLDs, consistently across
  both runs. Its constant HOLD votes read to the Portfolio Manager as a
  vote *against* trading, and over that window the trades it suppressed
  were profitable.
- **The sentiment node emits a continuous score (-1.0 to +1.0), not a
  BUY/HOLD/SELL vote.** Chosen over rewording the prompt while keeping
  the categorical shape, and over dropping the node outright, because it
  fixes the root cause of three separate failures at once:
  - Two LoRA fine-tunes (Financial PhraseBank, then distillation from
    GPT-4o-mini itself) both collapsed to a constant. On a ~96%-one-class
    target that IS the loss minimum — the data, not the hyperparameters,
    was the problem. A score has real variance even on routine news, so
    it is a trainable regression target and the Phase 04 fine-tuning goal
    survives.
  - The old prompt's *"prefer HOLD with lower confidence over guessing a
    direction"* line is what made the teacher near-constant. It is gone.
  - A near-zero score means "no directional information", which is
    honestly different from "I recommend not trading" — and
    `portfolio_manager.py`'s prompt is written to read it that way,
    explicitly, so the abstention-as-veto effect above can't come back.

  `agent_opinions.opinion` is free TEXT (no CHECK constraint), so the
  signed decimal string ("+0.30") needed no migration; the numeric value
  is also written to `raw_output->>'score'`. Rows from backtests 3-9
  still hold BUY/HOLD/SELL, so anything grouping on that column has to
  handle both shapes — `scripts/inventory_sentiment.py` does.
- **Any accuracy/agreement number gets compared to the majority-class
  baseline before it is believed.** Phase 04 produced two results — 84.0%
  and 90.9% — that both look like successes and are both *at or below*
  the trivial always-HOLD baseline for their datasets. On a ~96%
  single-class problem, headline accuracy is close to meaningless. For
  the regression version the equivalent is MAE against a
  predict-the-mean baseline, plus a sign-agreement rate.
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
  up to 3 days of headlines and rates their tone on a **continuous
  -1.0 to +1.0 score** (bearish to bullish). If there are zero headlines,
  it returns a neutral 0.00 **without calling the LLM at all** — cost
  control: nothing to read means nothing to pay for. It used to emit a
  BUY/HOLD/SELL vote — see "the sentiment node emits a continuous score"
  in the design decisions log for why that changed.
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

### Fixed gap: price_snapshots wasn't being written

Originally shipped with a known gap: nothing wrote to `price_snapshots`,
so the `positions` view's `current_price`/`unrealized_pnl` would be
`NULL`. That turned out worse in practice than "shows NULL" — during the
first live end-to-end run, `Position.current_price` isn't `Optional`, so
`GET /positions` after a real trade crashed with a 500 instead of just
displaying an empty field. Fixed by having `runner.py` write one
`price_snapshots` row per symbol per run (`_record_price_snapshot()`,
right after pricing the paper trade) — `open`/`high`/`low`/`volume` stay
`NULL` since this is a "last known price" record, not a real OHLCV bar,
and the schema already allows that.

### Live verification

Both external integrations have been confirmed against real API calls
(not just the fakes the test suite uses) — Alpaca price bars, Alpaca
news, and an OpenAI chat completion all returned real data. This had to
happen outside the usual dev environment: that sandbox's network policy
blocks `data.alpaca.markets` and `api.openai.com` entirely (same
category of restriction as the earlier Hugging Face/Docker Hub blocks),
so the actual verification ran on a Windows machine instead.

One real bug turned up and got fixed: `alpaca-py` defaults bar requests
to the SIP (consolidated) feed, which the free "Basic" market data plan
this project uses isn't allowed to query for recent data — it returned
`{"message":"subscription does not permit querying recent SIP data"}`.
Fixed by explicitly requesting `feed=DataFeed.IEX` in
`AlpacaPriceSource.get_recent_bars` (`app/agent/data_sources.py`), which
Basic accounts do get for free. The news endpoint's response shape
(`NewsSet.data["news"]`) needed no changes — the original assumption was
correct.

### Run it

Requires `OPENAI_API_KEY`, `ALPACA_API_KEY`, and `ALPACA_SECRET_KEY` in
`.env` (see `.env.example`).

```bash
uvicorn app.main:app --reload
curl -X POST http://127.0.0.1:8000/run/trigger
```

### Test it

```bash
pytest -q   # 36 tests. 31 run against fakes with zero services needed.
            # 5 (historical headlines + backtest runner) talk to a real
            # local Postgres and skip themselves if none is reachable —
            # see "Backtesting infrastructure" below for why those two
            # specifically need a real database instead of a fake.
```

## Backtesting infrastructure

The project's own gate ("the strategy logic must be honestly backtested
... before V2 begins") needs more than a for-loop over historical dates —
it needs backtest data and execution kept structurally separate from
live paper-trading state, or a backtest run would corrupt real positions.
Built so far:

- **`historical_headlines` table** (`004_historical_headlines.sql`) —
  imported once from the [FNSPID
  dataset](https://github.com/Zdong104/FNSPID_Financial_News_Dataset)
  (15.7M time-aligned news records, 1999-2023), not called as a live API
  during a backtest run. `HistoricalHeadlineSource`
  (`app/agent/data_sources.py`) reads from it with a strict
  `published_at < as_of` filter — look-ahead-bias safety, per
  `docs/backtesting-plan.md`'s checklist — and mirrors
  `AlpacaHeadlineSource`'s exact method signature, so agent nodes can't
  tell which one is active. Historical *prices* need no separate class:
  `AlpacaPriceSource` already accepts arbitrary historical `start`/`end`
  dates, so a backtest still makes real (rate-limited) Alpaca calls for
  price data — only headlines are local.

- **The live-table isolation decision.** `outcomes` and `price_snapshots`
  have no `mode`/`backtest_id` column, and the `positions` view
  (`002_positions_view.sql`) reads both directly with zero filtering. If
  backtest trades were written there the same way live trades are, every
  simulated fill would show up as a real open position, and every
  simulated historical price would be eligible to win "most recent
  price" — a backtest would silently corrupt real paper-trading state.
  So backtest fills get their own ledger, `backtest_outcomes`
  (`005_backtest_outcomes.sql`, same FIFO lot-accounting shape as
  `outcomes`), and their own risk-check repository, `BacktestRepository`
  (`app/repository/backtest.py`), scoped to one `backtest_id`. `runs` /
  `decisions` / `agent_opinions` stay shared with the live path (already
  tagged via `runs.mode`/`backtest_id`, `003_backtest_support.sql`) since
  nothing reads those without going through a specific `run_id`.

- **`app/agent/backtest.py`** — `run_backtest()` walks trading days
  (Mon-Fri, no market-holiday calendar yet — a documented simplification,
  not an oversight) across a date window, running one decision cycle per
  symbol per day and persisting it. Slippage is modeled as a basis-point
  cost against the decision price (default 5 bps; the plan's alternative
  — fill at the next bar's open — would need a second price fetch per
  symbol per day for comparatively little extra realism at this scale).
  Commission is modeled as $0, matching Alpaca's real pricing, stated
  explicitly rather than silently assumed. `compute_backtest_metrics()`
  reports realized P&L, trade count, and win rate from closed lots; it
  deliberately does *not* compute a buy-and-hold comparison itself (that
  needs a price-history lookup the caller already has via
  `PriceDataSource`) or mark-to-market open lots at window end (reported
  separately as `open_trades_at_window_end`, not folded into P&L).

- **`scripts/run_backtest.py`** — CLI entry point. Defaults to fixed
  neutral LLM stand-ins (sentiment score 0.00, Portfolio Manager HOLD; no
  `OPENAI_API_KEY` needed) so the pipeline itself — does it run, does it
  persist correctly — can be smoke-tested before spending real API
  budget; `--use-real-llms` switches to actual `ChatOpenAI` calls for an
  evaluation that means something. `--no-sentiment` is the ablation
  switch: it keeps the Portfolio Manager on real LLM calls but forces the
  Sentiment Analyst to a fixed neutral score, holding the graph structure
  constant while removing only the node's content.

- **`scripts/probe_sentiment_scores.py`** — pre-flight check on the
  sentiment node's score spread. Runs the real node over a sample of real
  headline days (default 25 gpt-4o-mini calls, a fraction of a cent) and
  prints the distribution plus a blunt verdict. Worth running before
  every set of paid backtest runs: if the scores cluster at 0.0 the node
  has collapsed to a constant again, and the P&L comparison those runs
  would produce isn't worth paying for.

**Open risk, not yet checked empirically:** the plan's in-sample window
starts at 2015, but `AlpacaPriceSource` is pinned to the `IEX` feed (the
free-tier fix from Phase 03's live verification) — and the IEX exchange
itself only launched in 2016. Whether IEX has usable daily bars back to
2015 for this project's symbols is unverified. If it doesn't, the
Technical Analyst's existing "not enough history" HOLD fallback means
this would fail quietly (a narrower effective in-sample window) rather
than loudly — worth a direct check before trusting 2015-2016 results.
