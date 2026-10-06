# Paper Trading Agent

[![CI](https://github.com/Pranav240/paper-trading-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Pranav240/paper-trading-agent/actions/workflows/ci.yml)

A paper-trading decision-support agent, built in phases to learn backend
API development, real database work, LLM fine-tuning, agentic
orchestration, containerized deployment, and cloud deployment — end to
end, on one real system rather than six disconnected exercises.

**Paper trading only.** This project never touches real capital or a live
brokerage account. The goal is an honestly evaluated system, not a claim
of profitability — if backtesting shows no edge, that is the correct,
reportable outcome.

## Status

Phases 01 (Control API), 02 (State & history), 03 (Decision agent) and 05
(Build pipeline) — done. Phase 06 (Scheduled run) — **applied to a real AWS
account, verified with a live decision cycle, then destroyed the same day**
rather than left billing to run a strategy with no edge. Three bugs that
`terraform validate` could never have caught turned up in that one apply;
`infra/README.md` records them. Phase 03 includes a full live end-to-end run
against a real Postgres database: real Alpaca market data + news, real
OpenAI reasoning from both LLM nodes, a correctly tracked real paper
position.

**Backtesting is done, and the headline result is negative: V1 has no
edge on AAPL.** FNSPID was imported, the runner was fixed (a psycopg
transaction bug meant no day's writes were visible, so the position cap
never engaged) and a three-window walk-forward was run against real data.
In-sample the system landed slightly *behind* buy-and-hold; out-of-sample,
run once with no changes made after seeing the in-sample result, it lost
$63 marked-to-market in a market that was essentially flat. Full numbers
in [Backtest results](#backtest-results--walk-forward-and-the-verdict),
including an offline replay showing that the drawdown rule everyone
assumes would have fixed it recovers only $14 of the $63. Per this
project's own stated rule, a negative result is the correct outcome to
report, not a failure to hide.

**Phase 04 (sentiment fine-tune) is closed on a negative result, and it
is where the interesting findings are.** Two LoRA adapters were trained
and both are unusable — each scored at or *below* the trivial
majority-class baseline, which is only visible if you check the baseline.
Ablating the sentiment node then showed the system was better off without
it. It was rewritten to emit a continuous score instead of a
BUY/HOLD/SELL vote; that rewrite works as specified — real spread, no
collapse — **and made trading worse**, +745.86 against a +896/+930 bar,
the worst configuration tested. Those are two separate facts and both are
reported.

**Phase 04 was then reframed and answered rather than abandoned.** The
suspect in every v1 failure was the label: imitating a teacher that said
HOLD 96.8% of the time. So v2 relabelled from price data — 41,701
examples, 104 symbols, 19 months, labels with real variance — and asked
whether headlines predict a stock's 5-day excess return at all. A TF-IDF
baseline found nothing (IC +0.002). A LoRA-fine-tuned Qwen2.5 with a
regression head found nothing either (IC -0.013 against a pre-registered
bar of +0.03). The model collapsed to predicting the mean, which is the
*correct* response to an input carrying no information — a different
thing from v1's collapse, where the labels themselves were degenerate.

Five attempts, two framings, one answer. That is a finding, not a gap.
Full detail, including a flaw found in the pre-registration itself, in
`docs/phase04-handoff.md`.

## Roadmap

**V1 — daily decision-support agent**

1. Control API — FastAPI endpoints for positions/decisions/trigger-run *(done)*
2. State & history — Postgres schema (watchlist, price snapshots, decisions, outcomes) *(done)*
3. Decision agent — LangGraph **multi-agent** flow (supervisor pattern):
   Technical Analyst + Sentiment Analyst report to a Portfolio Manager,
   with a Risk Manager able to veto/scale any trade → paper-trade → log *(done)*
4. Sentiment model — LoRA fine-tune on financial headlines (Hugging Face PEFT)
   *(answered, negatively — two failed adapters, an ablation, a rewrite, and
   a return-prediction regression on 41k examples that found no signal)*
5. Build pipeline — Dockerfile + GitHub Actions *(done)*
6. Scheduled run — deployed on AWS (EC2 + RDS), triggered once per session
   *(done — applied, ran one live cycle on EC2, then destroyed; see `infra/`)*

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
  / +840.45, without-node +896.15 / +929.84). Note the buy-and-hold
  baseline is +970.60 for the 281-day window those runs used, not the
  +903.80 originally quoted — that figure belongs to backtest 4's slightly
  longer 283-day window, and mixing the two made the without-node runs
  look like they matched buy and hold when they were $41-74 below it. The
  ablation's own comparison is run-to-run rather than against a baseline,
  so its direction is unaffected. Running
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
- **Replay recorded decisions offline before running anything new.** The
  Risk Manager is rule-based and every input it consumed is already in the
  database — the Portfolio Manager's proposals in `agent_opinions`, the
  price the agent saw in `decisions.technicals_snapshot`. So "what would a
  different risk rule have done?" is arithmetic over recorded data, not a
  new backtest: `scripts/replay_risk_rules.py`, zero API calls. Its
  baseline row replays the *existing* rules and must reproduce the recorded
  run or it refuses to print anything else — a replay that can't reproduce
  the past can't be trusted about hypotheticals. Applied to backtest 6 it
  killed the standing "a drawdown rule would have fixed it" hypothesis for
  free, and corrected the recorded diagnosis: the December
  buying-into-a-decline pattern is 43% of that loss, while 54% is a single
  September lot hit by a two-day 6.4% gap down that no add-to-position rule
  can see coming.
- **Assert two runs share a window before comparing their P&L.** Backtest
  4 ran 2022-06-01..2023-06-30 (283 days); runs 7/8/9/14 ran
  2022-06-03..2023-06-30 (281 days). AAPL fell 148.73 -> 145.39 across
  those two days, moving buy & hold by $66.80 on a 20-share basis. Every
  writeup compared the ablation runs against backtest 4's +903.80 and
  concluded they "matched buy and hold" — against their own window's
  +970.60 they were $41-74 below it, and **no configuration this project
  has ever run has beaten buy and hold.** The ablation's own conclusion
  survives, because that comparison was run-to-run rather than against a
  baseline. `scripts/compare_ablation.py` had printed the window for every
  run the entire time; it now prints a loud warning instead of trusting
  anyone to read it. A number that is merely *displayed* is not a number
  that has been *checked*.
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

## Backtest results — walk-forward, and the verdict

Three real-LLM runs inside the dense-coverage window FNSPID actually
provides for AAPL (Jun 2022 - Dec 2023), in chronological order:

| id | window | closed | open @ end | realized | mark-to-market | buy & hold (20sh) | vs. B&H |
|----|--------|--------|------------|----------|----------------|-------------------|---------|
| 3 | Q3 2022 (pilot) | 4 | 1 | +92.74 | **+6.21** | -15.40 | +21.61 |
| 4 | in-sample (Jun 22 - Jun 23) | 32 | 0 | +864.62 | **+864.62** | +903.80 | -39.18 |
| 6 | **out-of-sample (Jul - Dec 23)** | 2 | 3 | -36.01 | **-63.10** | +2.20 | -65.30 |

**Mark-to-market, not realized P&L, is the number to read.** Realized
alone made run 3 look like a clean 100% win rate and made run 6 look
better than it was — both had open lots sitting on unrealized moves.
`scripts/compare_ablation.py` computes this for any set of backtest ids.

**The verdict: no edge.** In-sample the system landed slightly behind
buy-and-hold — close enough to be noise either way with 32 trades on one
symbol. Out-of-sample, run once with no code, prompt or parameter changes
made after seeing the in-sample result, it lost $63 marked-to-market in a
market that was essentially flat. That out-of-sample discipline is what
makes the negative result trustworthy rather than another
adjust-and-re-run cycle.

The pipeline was verified as genuinely working across all three runs
before the result was believed (design decision 9): every agent produced
varied output, the Portfolio Manager visibly synthesized rather than
copying the Technical Analyst, and the Risk Manager's VETO/SCALE fired
sensibly. The negative result is about the strategy, not broken plumbing.

### Replaying the losses through different risk rules

`scripts/replay_risk_rules.py` re-gates every recorded Portfolio Manager
proposal through alternative Risk Manager rules. It costs nothing to run:
the Risk Manager is rule-based, the PM's proposals are in `agent_opinions`
and the price the agent saw is in `decisions.technicals_snapshot`, so the
whole thing is arithmetic over recorded data. Its baseline row replays the
*existing* rules and must reproduce the recorded run exactly, or it stops
— on backtest 6 it matches to $0.0003.

Run against backtest 6, to test the standing hypothesis that a
drawdown-based de-risking rule would have avoided most of the loss:

| rule | mark-to-market | vs. baseline |
|------|----------------|--------------|
| baseline (current rules) | -63.10 | +0.00 |
| no adding when down 2% | -63.10 | +0.00 |
| no adding when down 1% | -49.24 | +13.86 |
| stop-loss 5% | -64.94 | -1.84 |
| stop-loss 3% | -49.03 | +14.06 |
| trailing stop 5% | -64.94 | -1.84 |
| no-add 2% + stop 5% | -64.94 | -1.84 |
| min 3 trading days between buys | -49.24 | +13.86 |
| min 10 trading days between buys | -49.24 | +13.86 |

**The hypothesis is wrong, and the recorded diagnosis was half right.**
The best rule recovers $14 of a $63 loss; two rules make it worse; every
row is still ~$50 behind simply holding. Note that the two spacing rules
and the 1% drawdown rule all land on exactly -49.24: they block the same
two December buys by three different mechanisms, and $13.86 is simply
what those two lots were worth. The per-lot breakdown says why:

| lot | entry | P&L |
|-----|-------|-----|
| 2023-09-04 | 189.57 | **-34.39** |
| 2023-11-27 | 189.88 | -1.62 |
| 2023-12-19 | 197.02 | -13.23 |
| 2023-12-20 | 195.04 | -7.30 |
| 2023-12-21 | 194.80 | -6.56 |

The three straight December BUYs into a decline — the pattern originally
named as the cause — are 43% of the loss. The single largest contributor,
54%, is one September lot caught by a two-day 6.4% gap down (189.73 →
182.89 → 177.59) that no add-to-position rule can see coming. And the
December decline was only ~1.1% from the first buy to the third, which is
why a 2% no-add threshold changes literally nothing.

#### The same replay on backtest 14 (the score-node run)

Backtest 14's problem was churn — 62 closed trades against 32-33 for
every other configuration. Re-gating it costs nothing, and every rule
improves it, which was not true on backtest 6:

| rule | mark-to-market | vs. baseline |
|------|----------------|--------------|
| baseline (current rules) | +745.86 | +0.00 |
| no adding when down 1% | +861.03 | +115.16 |
| stop-loss 3% | +997.14 | +251.28 |
| trailing stop 5% | +1004.98 | +259.11 |
| **min 3 trading days between buys** | **+1201.53** | **+455.67** |
| min 10 trading days between buys | +765.26 | +19.39 |

Buy & hold on this window: +970.60. Spacing entries three trading days
apart is the only configuration in this project that has ever beaten it.

Two reasons that is not a result. It is **threshold-sensitive**: 3 days
gives +455.67 and 10 days gives +19.39, where on backtest 6 those two
settings were *identical*. A rule whose value collapses when you move the
knob has been fitted to a window, not discovered in it. And it does not
resolve the score-vs-prompt confound — spacing cuts clustered entries
whichever component caused them. What it does establish is the mechanism:
the score node's problem is that it trades too *much*, not that it trades
*wrongly*.

**These numbers are in-sample by construction** — the rules were chosen
after seeing this window's losses, on one symbol. A row that beats the
baseline has been *selected*, not validated. The honest use of the table
is to pick one hypothesis to test on a window it wasn't fitted to, and
the table's actual message is that none of these is worth that test.

### Is "no edge" a fact about the strategy, or about AAPL?

One symbol is not a result. Testing a second needs headlines, and
`scripts/fnspid_symbol_census.py` answers whether the 23GB FNSPID file on
disk can supply them — one streaming pass over the whole file, counting
per symbol **per month** rather than per year. That granularity is the
point: year totals are exactly what hid the five-month gap that made
`backtest_id=2` uninformative (design decision 8).

15,549,299 rows scanned. 4,508 symbols appear in Jun 2022 - Dec 2023, and
**108 cover all 19 months at >= 15 headlines/month** — AAPL 8,865, MSFT
8,331, TSLA 8,250, NVDA 6,801, then BRK / GOOG / DIS / AMD / XOM / CVX all
continuous. Headline supply is not the blocker.

Three things that list does not mean: it includes ETFs (SPY, QQQ) and
crypto (ETH), so it needs filtering to actual equities; headline coverage
is not price coverage, and each symbol still needs Alpaca IEX bars over
the same window; and a multi-symbol backtest is not free — roughly one
gpt-4o Portfolio Manager call per symbol per trading day, about $0.87 per
symbol over this window.

## Phase 05 — Build pipeline (Docker + GitHub Actions)

Until this phase the project was reproducible only by reading the README
carefully: start a bare `pta-postgres` container with the right flags,
create a venv, `python db/migrate.py`, apply `db/seed.sql` by hand, then
`uvicorn`. Five manual steps, each with its own way to be wrong.

```bash
docker compose up --build     # API + a fresh Postgres, schema and seed applied
docker compose down           # stop, keep the data
docker compose down -v        # stop and delete the data volume
```

Then http://127.0.0.1:8000/docs.

### What's in it

- **`Dockerfile`** — `python:3.13-slim` (matching the version developed
  against), runs as a non-root user, and copies `requirements.txt` on its
  own layer before the source so editing a `.py` file doesn't invalidate
  the pip-install layer. Slim rather than alpine because `psycopg-binary`,
  `numpy` and `pandas` all ship glibc wheels — musl would force a source
  build of all three for no benefit.
- **`.dockerignore`** — `data/` first and foremost. It holds the 23GB
  FNSPID CSV and the LoRA adapter zips; without excluding it every build
  ships 22GB to the daemon before running an instruction.
- **`docker-compose.yml`** — Postgres with a `pg_isready` healthcheck, and
  an API service that waits on `service_healthy` before running
  migrations. Postgres accepts TCP connections a second or two before it
  will answer queries, so without the wait the first `up` loses a race
  with the migration step.
- **`db/bootstrap.py`** — migrations then seed, both idempotent, so it can
  run on every container start. Kept out of `db/migrate.py` for the same
  reason `db/seed.sql` is kept out of `db/migrations/`: migrations define
  structure, seeding decides what data to start with.
- **`.github/workflows/ci.yml`** — the test suite against a **real
  Postgres service container**, plus a job that builds the image and
  imports the app inside it.

### The compose stack does not adopt the existing container

On the development machine, the bare `pta-postgres` container holds every
backtest, decision and the imported FNSPID headlines. The compose stack
uses a different container name and its own volume, deliberately, so
`docker compose down -v` can never destroy that work. The two can't both
bind host port 5432, so either stop the bare container first or run:

```bash
POSTGRES_PORT=5433 docker compose up --build
```

### Why CI runs a real database, and fails on a skip

Six tests (`test_backtest.py`, `test_historical_headline_source.py`) exist
to prove multi-table transactional writes land correctly and that a
backtest never touches the live `outcomes` / `price_snapshots` tables —
properties a fake pool cannot check. They skip themselves when no database
is reachable, so CI without a service container would report a green suite
while silently running 33 of 39 tests.

That isn't hypothetical. Those six tests had been **silently skipping on
Windows for the whole of Phase 03 and 04**, reporting "no reachable
Postgres" while Postgres was up and reachable. The cause was the
`ProactorEventLoop` incompatibility already fixed in `scripts/run_backtest.py`
(`2eb1b6f`) but never applied to the test suite: psycopg's pool raises
`PoolTimeout`, which subclasses `psycopg.OperationalError`, which the
skip guard caught. One line in `tests/conftest.py` fixed it.

So the CI job greps its own output and **fails if any test skipped**. A
green tick over a silently shortened suite is worse than a red one — that
is the entire lesson of the bug above, encoded so it can't recur.

## Phase 07 — Risk engine (V2), step 1: price look-ahead in backtests

**Found a leak. Every V1 backtest number is affected and needs a rerun.**

`run_backtest` decides at 12:00 UTC. `AlpacaPriceSource.get_recent_bars`
asked Alpaca for daily bars with `end=as_of`. Alpaca stamps a daily bar at
midnight New York (04:00 UTC summer, 05:00 UTC winter), so the decision
day's own bar, with its close, came back. `scripts/check_bar_timing.py`
confirmed it live on 2026-09-28 for AAPL:

| Decision day | Last bar returned (before fix) | Close | After fix |
|---|---|---|---|
| 2023-06-15 | 2023-06-15 04:00 UTC | 185.99 | 2023-06-14, 183.95 |
| 2023-12-14 | 2023-12-14 05:00 UTC | 198.16 | 2023-12-13, 197.86 |

This breaks the first rule of `docs/backtesting-plan.md`'s look-ahead
checklist (prior-day close only). The technical analyst was scoring each
day with that day's close already in hand.

**Fix:** `bars_closed_before()` in `app/agent/data_sources.py` keeps a bar
only if its session close (16:00 New York, DST-aware) is at or before
`as_of`. Early-close days are treated as 16:00, which can drop a final bar
but never leak one. Tests: `tests/test_alpaca_price_source.py`, using the
real timestamps above. Live runs are affected too: a mid-session run no
longer sees the in-progress bar for today.

**Not yet done:** the seven V1 backtests (and the results on `index.html`
and the dashboard) were produced with the leak. They have not been rerun.
Whether the leak helped or hurt the strategy is not measured; the exact
numbers, and the verdict drawn from them, are not valid until rerun.

## Phase 07 step 2: `risk_math.py`, and how much Kupiec can actually tell us

`app/agent/risk_math.py`: historical VaR/CVaR (95%, 1-day, 250 returns,
k-th largest loss with k = ceil(n x 5%) = 13, no interpolation), normal
VaR/CVaR for comparison, 60-day pairwise correlation on date-aligned
returns, and the Kupiec POF test. Plain Python, no scipy. Checked against
scipy locally (p-values within 5e-15; VaR/CVaR/correlation identical) and,
in `tests/test_risk_math.py`, against Jorion's published 95% acceptance
regions (T=255: 7-20, T=510: 17-35, T=1000: 38-64 breaches, all exact).

**Power check, before using the test** (exact binomial, alpha = 5%):

| Days | Accept (breaches) | Size | True rate 2.5% | 7.5% | 10% | 15% |
|---|---|---|---|---|---|---|
| 140 (~out-of-sample run) | 3-12 | 0.051 | 0.32 | 0.25 | 0.65 | 0.98 |
| 250 | 7-19 | 0.059 | 0.57 | 0.42 | 0.88 | 1.00 |
| 283 (run #4) | 8-21 | 0.055 | 0.59 | 0.46 | 0.92 | 1.00 |

What this means for step 6:

- Over run #4's 283 days, Kupiec reliably catches a model breaching 10%+
  of days (twice the target). It **misses a 7.5% model more often than
  not** (46% power). "Passed Kupiec" is weak evidence; a 50%-too-high
  breach rate usually passes.
- On the ~140-day out-of-sample window it is weaker still: 65% power even
  against a 10% breach rate.
- 99% would be worse: at 250 days the region is 1-6 breaches, size 9.5%,
  and only 24% power against a true 2% rate. Hence 95%.
- So a naive 2% VaR "beating" our VaR on Kupiec needs the breach counts
  and p-values reported side by side, not just pass/fail.

## Phase 07 step 3: VaR budget in the risk manager (pre-registered)

Fixed **before** any backtest was run with it, and without looking at
AAPL's VaR over the backtest windows:

- **Budget:** a full 20-share position may carry at most **2.0%** of its
  value as 95% 1-day historical VaR. Max shares = floor(20 x 2% / VaR),
  capped at 20. VaR at or under 2%: the budget never binds.
- **V1 rules first, unchanged**, as a hard floor. The budget only shrinks
  or vetoes a BUY that V1 allowed; it never enlarges a trade.
- **SELLs are never limited** by the budget.
- **Already over budget** (volatility rose after buying): further BUYs
  vetoed, `over_var_budget` flagged, **no forced sell**.
- **Under 250 returns of history:** V1 only, `var_unavailable` flagged.
- **Correlation** (60-day, with other held symbols): recorded, and
  `high_correlation:<SYM>` flagged above 0.8. Never changes quantity.

Everything lands in the risk manager's `raw_output`: VaR/CVaR (historical
and normal), `var_max_qty`, `correlations`, `risk_flags`. The node takes an
optional `price_source`; without one it is exactly the V1 node, which is
how the 7 original tests still run untouched.

## Phase 07 step 6: baselines

### Leak confirmed in the stored V1 runs, not just the API

Every stored V1 decision's `technicals_snapshot.current_price` was
compared with real IEX closes: **938 of 938** decisions in runs #4, #6, #9
and #14 used that day's own close; none used the prior day's. (Those runs
also made a decision on every weekday, including 11 market holidays in
run #4's window. Not a leak: there's no close that day. But those days
count as "decision days" in V1's totals.)

### 6a: historical VaR vs a naive 2% VaR (Kupiec, `scripts/evaluate_var.py`)

Criterion committed before the first run (`dff330f`): historical VaR
"beats" naive iff Kupiec does not reject it (p >= 0.05) and does reject
naive. AAPL, 95% 1-day, closes through D-1 only:

| Window | Days | Historical: breaches, p | Parametric | Naive 2% | Verdict |
|---|---|---|---|---|---|
| **Primary: run #4** (Jun 2022 to Jun 2023) | 272 | 12 (4.4%), p=0.65 | 12, p=0.65 | 28 (10.3%), p<0.001 | **beats naive** |
| Out-of-sample (Jul to Dec 2023) | 126 | 2 (1.6%), p=0.041 | 2, p=0.041 | 7 (5.6%), p=0.78 | **historical fails** |
| "Long" (Jul 2021 to Dec 2023) | 613 | 33 (5.4%), p=0.67 | 32, p=0.80 | 65 (10.6%), p<0.001 | beats naive |

- **Primary verdict: pass.** In run #4's window the 2% naive VaR was
  breached twice as often as it should be; the 250-day historical VaR was
  on target.
- **Out-of-sample it fails the other way: too conservative.** The 250-day
  window still carried 2022's volatility into a calm late 2023 (mean VaR
  2.7% vs a realized breach rate of 1.6%). For a budget this means
  shrinking positions more than the risk justified. Naive 2% happened to
  fit that calm stretch. At 126 days the test's size is 7% (not 5%), so
  this rejection is itself weak evidence, but it is reported as a fail,
  as pre-registered.
- **The "long window" is shorter than planned.** It was meant to be
  2017 to 2023; Alpaca's IEX feed only has AAPL bars from mid-2020, so
  the first forecast with 250 returns behind it is 2021-07-26. Reported
  as what it actually covered.
- Parametric and historical VaR are nearly indistinguishable here.

Stored forecasts were cross-checked against an independent recomputation
(`evaluate_var.py --check-backtest`): max difference 3.6e-7, the
NUMERIC(10,6) rounding.

### 6b: VaR node vs rule-based node, first attempt blocked (result further down)

Plan: one real-LLM rerun of run #4's config with the VaR node, then
replay both rule sets over the same recorded proposals
(`scripts/compare_var_node.py`, committed before the run finished,
`ee9d656`; the replay gate reproduces run #6 to $0.0003). The Portfolio
Manager never sees positions, so this isolates the risk node exactly.

The rerun (backtest 18) **stopped after 25 of ~272 days: the OpenAI
account ran out of credits** (HTTP 429 `credit_balance_exhausted`).
Backtest 18 is marked FAILED; its partial rows are not a result. Nothing
from it is reported. Step 6b, and therefore phase 07, is not done.

### Before the reruns: backtest fixes (`70b7b7f`), and a full dry run

Done while OpenAI credits were at zero, so the paid reruns happen once:

- **Real trading calendar.** Days come from Alpaca's market calendar. V1
  ran every weekday, 11 holidays included in run #4's window (283 vs 272).
- **Fills at the open.** A trade decided before the open on D now fills
  at D's open + 5 bps, as `docs/backtesting-plan.md` specified. V1 filled
  at D's own close (the leak); with only the leak fixed it would have
  filled at D-1's close, a price gone by the open. The open is stored per
  decision (`decisions.execution_price`, migration 007) so replays fill
  identically; old runs fall back to `current_price` and still reproduce
  (runs 4 and 6: drift $0.0004 / $0.0003).
- **Run setup recorded.** `backtests.config` holds models, risk settings,
  fill rule, calendar, git commit. A paid run refuses a dirty tree.
- **Crash = FAILED.** Backtests 5 (V1) and 18 had been left RUNNING; both
  now FAILED, and a crash now marks the run FAILED with tokens used so far.
- **Token usage measured.** Per model in `backtests.llm_usage`, via a
  LangChain callback passed into every graph call; a test drives it
  through LangGraph end to end.

**What this means for the V1 reruns:** they are V1 with three corrections
(leak, holidays, fill at the open), not the leak alone. Differences from
the published numbers can't be attributed to the leak alone.

**Dry run** (backtest 25, fake LLMs, VaR node, run #4's window): SUCCESS
in 210 s; 272 decisions, 0 on weekends or holidays, 272 opens, 272 VaR
forecasts matching an independent recomputation (max 4.8e-7); opens
spot-checked against raw Alpaca bars. Not exercised: real LLM calls
(token counts will be checked on the first paid run) and trade fills on
real prices (the fake Portfolio Manager always HOLDs; fills are covered by
`tests/test_backtest_v2.py`).

### Switching the LLM provider to Claude (V2 onward)

Both OpenAI and Anthropic balances were at zero; Claude credit is what
gets bought. Rather than swap one hard-coded client for another, the
provider became configuration: `app/agent/llm.py`, `LLM_PROVIDER` in
`.env`, default still OpenAI. Every backtest records provider, models,
temperature and thinking setting in `backtests.config`.

- **Models (chosen 2026-10-05):** Portfolio Manager `claude-sonnet-5-5`,
  Sentiment Analyst `claude-haiku-4-5`: the same large/small split V1 had
  with gpt-4o / gpt-4o-mini.
- **Settings:** Sonnet 5.5 runs with thinking off (`between_tools`; it
  rejects `disabled`) to match V1's non-reasoning setup. Sonnet 5.5
  rejects `temperature`, so the Portfolio Manager runs at the model
  default: unlike V1's temperature 0, two runs need not match exactly.
  Haiku 4.5 keeps temperature 0.
- **Structured output** uses Claude's native JSON-schema mode; the default
  forced-tool-call method returns a 400 on Sonnet 5.5.
- **Estimated cost** from run #14's measured prompt sizes (chars / 3.5,
  so +/-50%): ~$1.60 per 272-day run; step 6 plus every V1 rerun ~$8.
  The first paid run's `llm_usage` replaces this estimate.

**What this does to comparisons.** Results from here on are a
Claude-driven strategy. The V1 reruns change four things at once (look-
ahead leak, holidays, fill at the open, model), so a difference from the
published V1 numbers can't be attributed to any one of them; no bridge
run (same config on both providers) is possible without OpenAI credit.
The VaR-node comparison is unaffected: both sides replay one run.

**Rerun plan (decided 2026-10-05):** Sonnet 5.5 + Haiku 4.5; the two
repeat runs are skipped (#8 repeated #7, #9 repeated #4), which loses
the run-to-run noise check they provided. Estimated at ~$0.006 per full
trading day (Portfolio Manager ~$0.004, sentiment ~$0.002):

| Run | What | Days | Est. |
|---|---|---|---|
| step 6 | VaR node, #4's window, current code; V1-rules replay of it is the corrected #14 | 272 | ~$1.60 |
| #4 | categorical sentiment | 272 | ~$1.60 |
| #7 | sentiment ablated (PM calls only) | 272 | ~$1.10 |
| #6 | out-of-sample, categorical | 126 | ~$0.75 |
| #3 | pilot quarter, categorical | ~64 | ~$0.40 |
| | **total** | | **~$5.40** |

Open: #3, #4 and #6 used the categorical sentiment prompt, which the
Phase 04 rewrite removed. Rerunning them faithfully needs it restored
behind a setting.

**Categorical mode restored (`fa094e8`).** V1's BUY/SELL/HOLD sentiment
prompt and the Portfolio Manager prompt written for it are back, verbatim,
behind `--sentiment-mode categorical` (`app/agent/v1_categorical.py`).
Both were checked byte-for-byte against git (ba47f0a, 275b6e4^) and are
pinned by hash in `tests/test_v1_categorical.py`. Dry run over #3's window
(backtest 44, fake LLMs): 64 trading days, all 64 sentiment opinions
stored as votes, mode recorded in config.

**Reduced to fit a $5 budget, in this order:** step 6 (~$1.60), #6
(~$0.75), then check real cost from `llm_usage`, then #4 (~$1.60) if it
fits. #3 and #7 are not rerun for now; the homepage will say so rather
than leave their old numbers looking corrected.

### 6b result: VaR node vs V1 rules on the same decisions (backtest 45)

Backtest 45: run #4's window, current (score) sentiment, Claude (Sonnet
5.5 + Haiku 4.5), leak/holiday/open-fill fixes. It was killed by a tool
time limit at day 220 and resumed (`3b101bc`); the resume is recorded in
its config. Replay gate: recorded MtM +428.56, replayed +428.56.

| Same 272 decisions | MtM P&L | % of cap | vs buy & hold | Max drawdown | Avg shares |
|---|---|---|---|---|---|
| VaR node (recorded) | +428.56 | 14.3% | -409.24 | 579.15 | 10.8 |
| V1 rules (replayed) | +920.81 | 30.7% | **+83.01** | 790.17 | 17.0 |
| Buy & hold, 20 sh | +837.80 | 27.9% | | | 20 |

- **The VaR budget bound on all 272 days** (AAPL's VaR stayed above 2%
  throughout), so the node held ~64% of V1's average position.
- **As pre-registered, it cost P&L** (-$492) in a rising market. Its
  drawdown was smaller (-$211), but by less than its exposure was cut:
  return per dollar of drawdown 0.74 vs V1's 1.17. On this window the
  budget made the strategy worse, risk-adjusted too.
- **The forecasts themselves hold up:** 12 breaches in 272 days (4.4%,
  Kupiec p=0.65) vs the naive 2% VaR's 28 (10.3%, p=0.0004), matching
  step 6a exactly. The VaR is a good estimate; using it as a binding
  2% budget was the costly part.
- **V1's rules beat buy-and-hold here, by $83 (3%).** No V1 run did. Not
  evidence of an edge: one path, one symbol. (Corrected: this first said
  it was within V1's repeat-run spread, "$24-90"; the real replicate
  spread is $24-34, so $83 exceeds it. See the correction at the end.)
  It also can't be pinned on any one change (leak fix, holidays, open
  fills, Claude).

**Cost:** the resumed 52 days measured $0.46 (Sonnet 78k in / 17k out,
Haiku 97k / 7k tokens), ~$0.0088 per day, ~50% above the estimate. The
first 220 days' usage was lost with the killed process; extrapolated
~$1.94, so step 6 cost ~$2.40 of the $5 budget. Spending caps are now
mandatory on paid runs (`a2602e2`).

### V1 #6 rerun (backtest 71): out-of-sample, categorical sentiment, Claude

Jul-Dec 2023, `--sentiment-mode categorical` (V1's prompts verbatim),
Claude, leak/holiday/open-fill fixes, VaR node recorded; V1's rules
replayed over the same 126 decisions (gate: drift $0.0001). Cost measured:
$0.96 (Sonnet 138k in / 40k out, Haiku 207k / 15k), $0.0076 per day.

| Same 126 decisions | MtM P&L | vs buy & hold | Max drawdown | Avg shares |
|---|---|---|---|---|
| **V1 rules (corrected #6)** | **+66.35** | **+64.15** | 582.45 | 18.5 |
| VaR node | +123.20 | +121.00 | 397.39 | 13.9 |
| Buy & hold, 20 sh | +2.20 | | | 20 |
| *Original #6 (gpt-4o, leaky)* | *-63.10* | *-65.30* | | |

- **The published out-of-sample loss does not survive the corrections:**
  -$63 becomes +$66, in a flat market. That can't be attributed to any
  single change (leak, holidays, open fills, gpt-4o -> Claude). (Corrected:
  this first called ~$65 "within the noise V1's repeat runs showed
  ($24-90)"; see the correction at the end.) The honest reading: V1's loss
  does not survive, and one path on one stock does not show a gain either.
- **Here the VaR budget helped:** +$57 P&L and $185 less drawdown, with
  the budget binding on 116 of 126 days. In-sample (backtest 45) it hurt
  (-$492). Same rule, opposite signs on two windows: no evidence either
  way that the budget improves the strategy.

**Budget:** ~$3.36 of $5 spent (step 6 ~$2.40 incl. extrapolated $1.94,
#6 $0.96). #4 (~$2.1-2.4 at measured rates) does not fit; #3, #4, #7
remain not rerun.

### Correction: the "within noise" claim was wrong

The two entries above, and the homepage, report, dashboard, README and
PROJECT.md as first updated, called the corrected results (+$64, +$83 vs
buy-and-hold) "within the run-to-run noise of V1's repeat runs ($24-90)".
The $90 came from comparing #4's and #9's *gaps to buy-and-hold*, which
used different baselines (the two-day window difference above), so it is
not run-to-run noise. The actual replicate spread is **$24-34**: #7 vs #8
(same window) differ by $33.69, #4 vs #9 by $24.17.

So the corrected gaps are two to three times model-randomness noise. That
still doesn't make them an edge: each is one run on one price path, on
one stock, after four simultaneous changes, and replicates don't sample
the price path, which dominates any buy-and-hold comparison. Every page
now says the narrower, true thing: V1's "underperforms" does not survive
the correction, and an edge is not established.

## Phase 08 — Explainer agent, built with fakes

A node after the risk manager that says, in plain language, why the VaR
budget cut or blocked a trade. Pre-registered design (module docstring of
`app/agent/explainer.py`, written before any real call):

- **Trigger:** `var_budget_scale` / `var_budget_veto` only: the budget
  changed the trade. It explains; it never alters a decision.
- **What it explains:** the 5 worst losses the VaR is built from. The risk
  node now records them (`var_tail`, via `risk_math.tail_losses`), so the
  explanation is about the number actually used, not this week's news.
- **Retrieval:** Postgres full-text search over `historical_headlines`,
  window [D-1, D+1) New York and strictly before `as_of`, ranked by
  relevance to the company and fixed market terms; 8 per day. Baseline:
  same window by recency only (`--explain recent`).
- **Grounding check:** every cited headline must have been retrieved for
  that same day; problems are stored and `citations_valid` set false. The
  explanation is kept, never repaired, so the failure rate is measurable.
- **Baseline explanation:** a no-news, no-model template with the same
  facts, stored next to every explanation, for phase 09 to compare.
- **Storage:** `risk_explanations` (migration 008). Model: Haiku 4.5.

Tests: 17 new (110 total): trigger, grounding check, template, node with
fake LLMs (including an invented citation kept and flagged), retrieval
SQL against real Postgres (ranking, New York day bounds, nothing after
`as_of`), the real graph end to end, and persistence through a backtest.

**Retrieval-only dry run** (`scripts/explainer_retrieval_check.py`, no LLM):

| | bt 45 (in-sample) | bt 71 (out-of-sample) |
|---|---|---|
| VaR-changed decisions | 105 | 49 |
| Driver days with any headline | 253 / 525 (48%) | 245 / 245 (100%) |
| Top headline names Apple: fts vs recent | 59 vs 21 | 128 vs 75 |
| fts / recent overlap in picks | 31% | 30% |
| Est. prompt / cost per call (Haiku) | ~870 tok / $0.0026 | ~1,315 tok / $0.0031 |

- Half the in-sample driver days have **no news at all**: the worst losses
  include May 2022, inside FNSPID's gap. The "say so, cite nothing" rule
  will carry much of the in-sample load.
- Full-text ranking surfaces an Apple-named headline first 2.8x (in) and
  1.7x (out) as often as recency. "Names Apple" is a crude proxy for
  relevance; phase 09's hand labels are the real test.
- Not yet done: one small real run (~20 decisions, ~$0.06) to measure the
  share of explanations whose citations hold up.

### Phase 08 real run: 20 recorded decisions (`scripts/explain_recorded.py`)

Sample pre-registered and committed before running (`aaca37e`): 10
evenly spaced VaR-changed decisions each from backtests 45 and 71, fts
retrieval, Claude Haiku 4.5, $0.15 cap. Only the explainer was paid for;
decision states were rebuilt from what the backtests stored.

| | Result |
|---|---|
| Explanations written | 20 / 20 |
| All citations grounded (retrieved for that same day) | **20 / 20** |
| Citations per explanation | 7.4 |
| No-news driver days left uncited | 27 / 27 |
| Tokens (Haiku 4.5) | 33,955 in / 6,014 out |
| Cost, measured | **$0.064** |

**What the check does not cover.** "Grounded" means every cited headline
was one the model was shown for that day; it does not mean the headline
supports the stated cause. A manual spot check of one explanation
(2023-11-28, 5 driver days, 11 citations): 4 of 5 causes match their
headlines closely (China iPhone curbs, iPhone slump, hawkish Fed, Apple
falling at the start of 2023); one embellishes — 2022-12-28 says "weak
economic data" where both cited headlines say recession fears and weak
*oil prices*. Phase 09's grading has to catch this kind of drift; the
citation check cannot.

The template baseline in these 20 rows reads "1 shares" where the cut was
to one share; fixed since in the code (rows left as written).

Budget: ~$3.42 of the $5 spent; ~$1.58 left.

## Phase 09 — deviation from the pre-registration (2026-10-06), before any label

The plan (`docs/phase09-plan.md`, section 2) says the 40 test days are
labelled by the project owner, by hand. **Changed at the owner's request:
the labels are written by Claude (Opus 5.5), the coding assistant.**
Recorded here, before any label exists, as the plan requires.

What this costs, stated plainly:

- **Independence.** The explainer (Haiku 4.5) and the judge (Sonnet 5.5)
  are Claude models too. Labels from the same model family share its
  blind spots, so agreement between explainer, judge and labels is weaker
  evidence than agreement with a person would be.
- **Contamination.** The labeller had already seen the explainer's
  output and cited headlines for 5 test days during the phase 08 spot
  check (2022-12-15, 2022-12-28, 2023-01-03, 2023-08-04, 2023-09-06).
  Those days are flagged in `eval/labels.json` (`seen_explainer_output`)
  so results can be reported with and without them.
- **Mitigations.** Labelling from the blind view only (headline text and
  time, shuffled, no retrieval method or rank — the same view the page
  shows); by the plan's written rules; before any new explanation exists.
  The retrieval test (fts vs recency) is affected least: neither method
  is a language model.
- **Judge check (section 5).** If the same labeller also scored the 20
  judge items, the check would be Claude agreeing with Claude. Proposed:
  the owner still scores those 20 (~10 minutes); otherwise the judge
  check is reported as LLM-only and not counted as validation.

## A second look-ahead leak: date-only headline timestamps (found 2026-10-06)

Found while preparing phase 09's labels: every candidate headline was
stamped at exactly midnight UTC. Across `historical_headlines`, **99.66%
of rows are stamped 00:00 UTC** (AAPL: 100% in 2022, 99.6% in 2023). The
FNSPID `Date` field carries a date, not a time of publication.

**Consequence 1 — sentiment look-ahead in every backtest.** The headline
filter is `published_at < as_of` with `as_of` = D 12:00 UTC (08:00 New
York, before the open). A headline dated D is stamped D 00:00 UTC and
passes, whenever on D it was actually published. Measured from stored
opinions, the share of decisions whose sentiment input included
headlines dated that same day:

| Run | #4 | #6 | #9 | #14 | **45** | **71** |
|---|---|---|---|---|---|---|
| Same-day headlines seen | 100% | 98% | 100% | 100% | **100%** | **98%** |

Concrete case, run #14 and corrected run 45, decision at 2022-06-03 08:00
New York: the sentiment analyst read "Apple Was the Worst Stock in the Dow
Friday", "US STOCKS-Wall St ends down with strong jobs data…" and "Why
Nvidia, Amazon, and Apple Stocks Slumped Friday" — all published after
that day's close. **This affects the two "corrected" reruns as well**:
their +$64 / +$83 against buy-and-hold were produced with it.

The backtesting plan's headline rule, `published_at < as_of` strictly,
was correct for timestamps with times and silently wrong for dates.

**Consequence 2 — the explainer's window is a day late.** The window
[D-1 00:00, D+1 00:00) New York is [D-1 04:00, D+1 04:00) UTC in summer,
so with midnight-UTC stamps it holds headlines *dated D and D+1*, not
D-1 and D. Phase 08's coverage numbers, its 20 explanations and phase
09's candidate set were all built on the shifted window. No phase 09
label exists yet.

Not affected: the VaR forecasts and Kupiec results (prices only), and
the VaR-node vs V1-rules comparisons as risk-rule comparisons (both sides
replay the same decisions) — though those decisions were themselves made
with the leak.

### Phase 09 — second deviation: test set rebuilt on headline dates (before any label)

`docs/phase09-plan.md` defines each day's candidates by the explainer's
window "[D-1, D+1) New York". On FNSPID's midnight-UTC date stamps that
window held headlines *dated D and D+1* (see the headline-date finding
above). Fixed in the explainer, so the test set was rebuilt with the
corrected window, headlines dated D-1 or D, by the same selection rule
otherwise: still 40 days (13 of them explainer driver days), 498
candidates (was 520), 1,415 fixture headlines. No label existed for the
first version.

Fixes and public corrections for the leak itself: `6bebc48`, `fd99811`.
