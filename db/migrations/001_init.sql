-- 001_init.sql
--
-- Core schema. Read this top to bottom; the table order is deliberate
-- because Postgres won't let you create a foreign key pointing at a
-- table that doesn't exist yet.
--
-- Design notes worth internalizing (not just copying):
--
-- 1. watchlist.symbol is the join key everywhere. Making it TEXT with a
--    UNIQUE constraint (not a symbol_id surrogate key) is a deliberate
--    choice: symbols are stable, human-meaningful identifiers, and
--    joining on "AAPL" instead of some internal integer id keeps every
--    query below readable. This is a place a real system might disagree
--    (surrogate keys are more common), but for a learning project,
--    readable joins win.
--
-- 2. JSONB columns (technicals_snapshot, sentiment_snapshot, raw_output)
--    exist because the *shape* of a technical indicator dump or a raw
--    LLM response will keep changing as you iterate on the agent, and
--    you don't want a schema migration every time you add one field to
--    what an agent returns. JSONB is queryable (you can index into it,
--    e.g. technicals_snapshot->>'rsi_14') but flexible. The tradeoff:
--    you lose the enforcement a real column gives you (there's nothing
--    stopping a bad write from putting garbage in there) — that's a
--    conscious tradeoff, not an oversight.
--
-- 3. CHECK constraints (action, status, agent_name) are doing the job
--    Pydantic's Enum did in Phase 01 — but at the database layer. Two
--    layers of validation isn't redundant: Pydantic protects the API
--    boundary, CHECK protects the database from any other writer
--    (a future script, a manual psql session, a bug).

CREATE TABLE watchlist (
    symbol      TEXT PRIMARY KEY,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    active      BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE price_snapshots (
    id          BIGSERIAL PRIMARY KEY,
    symbol      TEXT NOT NULL REFERENCES watchlist(symbol),
    captured_at TIMESTAMPTZ NOT NULL,
    open        NUMERIC(12, 4),
    high        NUMERIC(12, 4),
    low         NUMERIC(12, 4),
    close       NUMERIC(12, 4) NOT NULL,
    volume      BIGINT,
    source      TEXT NOT NULL DEFAULT 'unknown',
    UNIQUE (symbol, captured_at)
);

CREATE INDEX idx_price_snapshots_symbol_time
    ON price_snapshots (symbol, captured_at DESC);

-- One row per agent decision cycle (matches Phase 01's RunResult).
CREATE TABLE runs (
    id          BIGSERIAL PRIMARY KEY,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status      TEXT NOT NULL DEFAULT 'RUNNING'
                CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED'))
);

-- The final call for one symbol within one run.
CREATE TABLE decisions (
    id                   BIGSERIAL PRIMARY KEY,
    run_id               BIGINT NOT NULL REFERENCES runs(id),
    symbol               TEXT NOT NULL REFERENCES watchlist(symbol),
    action               TEXT NOT NULL CHECK (action IN ('BUY', 'SELL', 'HOLD')),
    confidence           REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    reasoning            TEXT NOT NULL,
    technicals_snapshot  JSONB NOT NULL DEFAULT '{}'::jsonb,
    sentiment_snapshot   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_decisions_symbol_time
    ON decisions (symbol, created_at DESC);

-- One row per specialist agent's opinion, per decision. This is the
-- table added specifically for the multi-agent (Phase 03) redesign —
-- it's what makes "why did the Portfolio Manager decide this" answerable.
CREATE TABLE agent_opinions (
    id           BIGSERIAL PRIMARY KEY,
    decision_id  BIGINT NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    agent_name   TEXT NOT NULL
                 CHECK (agent_name IN (
                     'technical_analyst', 'sentiment_analyst',
                     'risk_manager', 'portfolio_manager'
                 )),
    opinion      TEXT NOT NULL,       -- free text on purpose: a Risk
                                       -- Manager's opinion ("VETO",
                                       -- "APPROVE") isn't the same
                                       -- vocabulary as an analyst's
                                       -- ("BUY"/"SELL"/"HOLD"); forcing
                                       -- one CHECK list across all agent
                                       -- types would be the wrong kind
                                       -- of strictness here.
    confidence   REAL CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    reasoning    TEXT NOT NULL,
    raw_output   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agent_opinions_decision
    ON agent_opinions (decision_id);

-- How a decision actually performed. This is what real backtesting and
-- the "refine the sentiment model against real outcomes" goal both read
-- from — without this table, "did the agent's calls actually work" is
-- unanswerable.
CREATE TABLE outcomes (
    id            BIGSERIAL PRIMARY KEY,
    decision_id   BIGINT NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    symbol        TEXT NOT NULL REFERENCES watchlist(symbol),
    quantity      INTEGER NOT NULL CHECK (quantity > 0),
    entry_price   NUMERIC(12, 4) NOT NULL,
    exit_price    NUMERIC(12, 4),
    opened_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at     TIMESTAMPTZ,
    realized_pnl  NUMERIC(12, 4),
    status        TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED'))
);

CREATE INDEX idx_outcomes_symbol_status
    ON outcomes (symbol, status);
