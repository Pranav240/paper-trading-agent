-- 003_backtest_support.sql
--
-- Adds what the backtesting plan (docs/backtesting-plan.md) said Phase 03
-- needs before any agent code gets written: a way to tell a live decision
-- cycle apart from a backtest one, and a way to group many backtest
-- "days" into one backtest execution.
--
-- Two different timestamps on `runs`, and the distinction matters:
--   - started_at / finished_at: when this run ACTUALLY executed (real
--     wall-clock time, whether that's a live run or a backtest run).
--   - as_of: what date this run's decision is FOR. For a live run,
--     as_of is essentially "now." For a backtest run simulating March
--     2022, as_of is a date in March 2022 even though started_at is
--     whenever the backtest actually happened to execute (could be
--     today). This is what every agent function reads instead of
--     calling datetime.now() internally — see the plan doc.

CREATE TABLE backtests (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    window_start  DATE NOT NULL,
    window_end    DATE NOT NULL,
    config        JSONB NOT NULL DEFAULT '{}'::jsonb,  -- symbols, params tuned, etc.
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    status        TEXT NOT NULL DEFAULT 'RUNNING'
                  CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
    CHECK (window_end >= window_start)
);

ALTER TABLE runs
    ADD COLUMN mode        TEXT NOT NULL DEFAULT 'LIVE' CHECK (mode IN ('LIVE', 'BACKTEST')),
    ADD COLUMN backtest_id BIGINT REFERENCES backtests(id),
    ADD COLUMN as_of       TIMESTAMPTZ;

-- A backtest run must belong to a backtest; a live run must not — this
-- is the constraint that makes "which rows are real paper-trading
-- decisions vs. which are backtest simulation" unambiguous at the
-- database level, not just a convention someone has to remember.
ALTER TABLE runs
    ADD CONSTRAINT runs_mode_backtest_id_consistent CHECK (
        (mode = 'BACKTEST' AND backtest_id IS NOT NULL) OR
        (mode = 'LIVE' AND backtest_id IS NULL)
    );

-- Backfill as_of for existing rows (Phase 02's stub runs) so the column
-- is never NULL going forward for live runs either.
UPDATE runs SET as_of = started_at WHERE as_of IS NULL;
ALTER TABLE runs ALTER COLUMN as_of SET NOT NULL;

CREATE INDEX idx_runs_backtest_id ON runs (backtest_id) WHERE backtest_id IS NOT NULL;
CREATE INDEX idx_runs_mode ON runs (mode);
