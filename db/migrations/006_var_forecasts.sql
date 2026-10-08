-- 006_var_forecasts.sql
--
-- One row per VaR forecast the risk manager makes (V2, phase 07), so the
-- forecasts can be backtested (Kupiec POF) against what actually happened.
--
-- The numbers already live in agent_opinions.raw_output as JSON; this
-- table exists because the evaluation needs to query and join them
-- (breach counts per backtest, per symbol) and to record the realized
-- return afterwards, which JSON-in-an-opinion-row is the wrong home for.
--
-- Timing, which is what makes a breach meaningful: a forecast made for
-- decision day D (as_of = D, 12:00 UTC, before the open) uses closes
-- through D-1 only (bars_closed_before in app/agent/data_sources.py).
-- It forecasts the 1-day return close(D) / close(D-1) - 1. `last_close`
-- is close(D-1); `realized_return` is filled in once close(D) exists
-- (app/agent/var_forecasts.py), never at forecast time.
--
-- backtest_id is nullable: a live run's forecasts belong to no backtest
-- (same LIVE/BACKTEST split as runs.backtest_id, 003_backtest_support.sql).
-- Rows are written only when a VaR was computable; "not enough history"
-- is a risk_flag in raw_output, not a row with NULL numbers.

CREATE TABLE var_forecasts (
    id                BIGSERIAL PRIMARY KEY,
    decision_id       BIGINT NOT NULL UNIQUE REFERENCES decisions(id) ON DELETE CASCADE,
    backtest_id       BIGINT REFERENCES backtests(id) ON DELETE CASCADE,
    symbol            TEXT NOT NULL,
    as_of             TIMESTAMPTZ NOT NULL,
    confidence        NUMERIC(4, 3) NOT NULL CHECK (confidence > 0 AND confidence < 1),
    window_size       INTEGER NOT NULL CHECK (window_size > 0),
    -- Losses as positive fractions: 0.021 = a 2.1% one-day loss.
    var               NUMERIC(10, 6) NOT NULL,
    cvar              NUMERIC(10, 6) NOT NULL,
    parametric_var    NUMERIC(10, 6) NOT NULL,
    parametric_cvar   NUMERIC(10, 6) NOT NULL,
    var_budget        NUMERIC(6, 4) NOT NULL,
    var_max_qty       INTEGER NOT NULL CHECK (var_max_qty >= 0),
    -- Shares held once this decision's trade (if any) is applied.
    position_qty      INTEGER NOT NULL CHECK (position_qty >= 0),
    last_close        NUMERIC(12, 4) NOT NULL,
    risk_flags        TEXT[] NOT NULL DEFAULT '{}',
    realized_return   NUMERIC(10, 6),
    -- NULL until realized_return is known.
    breach            BOOLEAN GENERATED ALWAYS AS (realized_return < -var) STORED,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- "All forecasts for backtest Y (and symbol X), in date order" is the
-- evaluation's only query shape; live forecasts share the index with a
-- NULL backtest_id.
CREATE INDEX idx_var_forecasts_backtest_symbol_as_of
    ON var_forecasts (backtest_id, symbol, as_of);
