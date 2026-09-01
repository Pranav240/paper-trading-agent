-- 004_historical_headlines.sql
--
-- Storage for the FNSPID dataset (docs/backtesting-plan.md), imported
-- once from a static CSV, never called as a live API during a backtest.
--
-- Deliberately NOT a foreign key into `watchlist`. The live watchlist is
-- "what we're trading today" and can change over time; historical
-- headlines are "what was published, for whichever symbols FNSPID
-- covers" — a fixed historical record that shouldn't be constrained by,
-- or coupled to, today's watchlist membership. A backtest can cover a
-- symbol that isn't on the live watchlist at all.
--
-- This is what makes look-ahead-safe headline filtering possible: a
-- HistoricalHeadlineSource (app/agent/data_sources.py) queries this
-- table for `published_at < as_of`, mirroring the live
-- AlpacaHeadlineSource's contract exactly, so agent nodes can't tell
-- (and don't need to know) which implementation is running underneath.

CREATE TABLE historical_headlines (
    id            BIGSERIAL PRIMARY KEY,
    symbol        TEXT NOT NULL,
    published_at  TIMESTAMPTZ NOT NULL,
    headline      TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'fnspid',
    -- Import scripts are expected to run more than once (re-imports,
    -- partial re-runs after a crash) — this is what makes that
    -- idempotent (ON CONFLICT DO NOTHING) instead of duplicating rows.
    UNIQUE (symbol, published_at, headline)
);

-- The only query pattern this table serves: "headlines for symbol X,
-- published before some as_of timestamp, most recent first" — matches
-- HistoricalHeadlineSource.get_recent_headlines() exactly.
CREATE INDEX idx_historical_headlines_symbol_time
    ON historical_headlines (symbol, published_at DESC);
