-- Export of the sentiment node's own calls, for evaluating/distilling a
-- local model against them.
--
-- `gpt4o_opinion` is kept for the legacy categorical rows (backtests 3-9
-- wrote 'BUY'/'HOLD'/'SELL' there). `gpt4o_score` is the continuous
-- score written since the 2026-09-08 rewrite; it is NULL on legacy rows.
-- The regression fine-tune trains on `gpt4o_score`, so filter to rows
-- where it is non-NULL before using this for training.
--
-- Adjust the backtest_id IN-clause to whichever runs you want exported.
SELECT json_agg(row_to_json(t)) FROM (
    SELECT
        d.symbol,
        r.as_of,
        ao.opinion                          AS gpt4o_opinion,
        (ao.raw_output->>'score')::float    AS gpt4o_score,
        ao.confidence                       AS gpt4o_confidence,
        ao.reasoning                        AS gpt4o_reasoning,
        ao.raw_output->'headlines'          AS headlines,
        r.backtest_id
    FROM agent_opinions ao
    JOIN decisions d ON ao.decision_id = d.id
    JOIN runs r ON d.run_id = r.id
    WHERE ao.agent_name = 'sentiment_analyst'
      AND r.backtest_id IN (3, 4, 6)
      AND (ao.raw_output->>'n_headlines')::int > 0
    ORDER BY r.as_of
) t;
