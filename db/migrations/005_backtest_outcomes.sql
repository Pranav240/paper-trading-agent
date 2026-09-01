-- 005_backtest_outcomes.sql
--
-- Backtest trade execution needs its own ledger, structurally isolated
-- from the live paper-trading tables — not just "remember to filter."
--
-- `outcomes` and `price_snapshots` have no backtest_id/mode column at
-- all, and the `positions` view (002_positions_view.sql) reads both of
-- them directly with zero filtering. If a backtest run wrote into those
-- two tables the same way a live run does, every simulated backtest
-- trade would show up as a real open position, and every simulated
-- historical price would be eligible to win the "most recent price"
-- lookup that GET /positions depends on — a backtest would silently
-- corrupt real live paper-trading state the next time someone checked
-- their positions. `runs` / `decisions` / `agent_opinions` don't have
-- this problem (003_backtest_support.sql already tags them with
-- mode/backtest_id, and nothing reads them except by a specific run_id),
-- so those stay shared. Trade execution and pricing do not.
--
-- Shape mirrors `outcomes` (001_init.sql) — same FIFO lot accounting,
-- see app/agent/backtest.py — plus backtest_id, and no FK on symbol
-- into watchlist for the same reason 004 didn't: decisions.symbol
-- already has that FK today (so in practice a backtest is still limited
-- to watchlist symbols), but backtest_outcomes shouldn't be the table
-- that enforces it, since a backtest is explicitly allowed to cover
-- symbols outside today's live watchlist per docs/backtesting-plan.md.

CREATE TABLE backtest_outcomes (
    id            BIGSERIAL PRIMARY KEY,
    backtest_id   BIGINT NOT NULL REFERENCES backtests(id) ON DELETE CASCADE,
    decision_id   BIGINT NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    symbol        TEXT NOT NULL,
    quantity      INTEGER NOT NULL CHECK (quantity > 0),
    entry_price   NUMERIC(12, 4) NOT NULL,
    exit_price    NUMERIC(12, 4),
    opened_at     TIMESTAMPTZ NOT NULL,
    closed_at     TIMESTAMPTZ,
    realized_pnl  NUMERIC(12, 4),
    status        TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED'))
);

-- Same query pattern as idx_outcomes_symbol_status, scoped one level
-- deeper: "open lots for symbol X, within backtest Y" is what both
-- BacktestRepository.list_positions() and the FIFO SELL logic need.
CREATE INDEX idx_backtest_outcomes_backtest_symbol_status
    ON backtest_outcomes (backtest_id, symbol, status);
