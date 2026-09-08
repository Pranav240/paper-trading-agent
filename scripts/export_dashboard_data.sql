SELECT json_build_object(
  'backtests', (
    SELECT json_agg(row_to_json(b)) FROM (
      SELECT id, name, window_start, window_end, status
      FROM backtests WHERE id IN (3,4,6) ORDER BY id
    ) b
  ),
  'daily', (
    SELECT json_agg(row_to_json(x)) FROM (
      SELECT r.backtest_id, r.as_of,
             (d.technicals_snapshot->>'current_price')::numeric AS price,
             d.action, d.confidence
      FROM decisions d JOIN runs r ON d.run_id = r.id
      WHERE r.backtest_id IN (3,4,6)
      ORDER BY r.backtest_id, r.as_of
    ) x
  ),
  'trades', (
    SELECT json_agg(row_to_json(y)) FROM (
      SELECT backtest_id, quantity, entry_price, exit_price,
             opened_at, closed_at, status, realized_pnl
      FROM backtest_outcomes
      WHERE backtest_id IN (3,4,6)
      ORDER BY backtest_id, opened_at
    ) y
  ),
  'opinions', (
    SELECT json_agg(row_to_json(z)) FROM (
      SELECT r.backtest_id, ao.agent_name, ao.opinion, COUNT(*) AS cnt
      FROM agent_opinions ao
      JOIN decisions d ON ao.decision_id = d.id
      JOIN runs r ON d.run_id = r.id
      WHERE r.backtest_id IN (3,4,6)
      GROUP BY 1,2,3
      ORDER BY 1,2,3
    ) z
  )
);
