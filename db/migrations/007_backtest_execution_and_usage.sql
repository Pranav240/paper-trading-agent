-- 007_backtest_execution_and_usage.sql
--
-- Two additions made before the V1 reruns (V2, phase 07), so those runs
-- record everything needed to trust and reproduce them.
--
-- decisions.execution_price: the price a trade on this decision fills at,
-- before slippage. A backtest decision is made before the open
-- (as_of = D 12:00 UTC) from closes through D-1, so its trade now fills
-- at day D's OPEN, as docs/backtesting-plan.md specifies ("model the fill
-- at the next bar's open"). V1 filled at technicals_snapshot.current_price,
-- which was day D's own close (the look-ahead leak), and after the leak
-- fix would have been D-1's close: a price already gone by the time the
-- market opened. Stored per decision, trade or not, so offline replays
-- (scripts/replay_risk_rules.py, compare_var_node.py) can fill any rule's
-- trades at the same price the real run would have. NULL for live runs
-- and for every decision made before this migration; replays fall back to
-- current_price there, which is how the old runs still reproduce.
--
-- backtests.llm_usage: tokens used per model, summed over the run
-- (LangChain usage_metadata), written at the end of the run and on
-- failure. So a run's cost is measured, not estimated after the fact.

ALTER TABLE decisions ADD COLUMN execution_price NUMERIC(12, 4);

ALTER TABLE backtests ADD COLUMN llm_usage JSONB;
