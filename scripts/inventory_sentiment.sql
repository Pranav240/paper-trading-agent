-- What sentiment_analyst data actually exists across ALL backtests.
-- Purpose: decide whether we have enough real BUY/SELL examples to
-- retrain the distilled model, or whether new backtest runs are needed.
--
-- `agent_opinions.opinion` is free TEXT and now holds two shapes:
-- legacy 'BUY'/'HOLD'/'SELL' rows from backtests 3-9, and signed decimal
-- strings ('+0.30') from the 2026-09-08 continuous-score rewrite. The
-- `scored` column counts the latter. `scripts/inventory_sentiment.py` is
-- the runnable version of this and also prints the score distribution.
SELECT
    r.backtest_id,
    COUNT(*)                                              AS total,
    COUNT(*) FILTER (WHERE ao.opinion = 'BUY')            AS buy,
    COUNT(*) FILTER (WHERE ao.opinion = 'SELL')           AS sell,
    COUNT(*) FILTER (WHERE ao.opinion = 'HOLD')           AS hold,
    COUNT(*) FILTER (
        WHERE ao.raw_output->>'score' IS NOT NULL
    )                                                     AS scored,
    ROUND(AVG((ao.raw_output->>'score')::numeric), 3)     AS score_mean,
    ROUND(
        STDDEV_SAMP((ao.raw_output->>'score')::numeric), 3
    )                                                     AS score_stddev,
    COUNT(DISTINCT d.symbol)                              AS symbols,
    MIN(r.as_of)::date                                    AS first_day,
    MAX(r.as_of)::date                                    AS last_day
FROM agent_opinions ao
JOIN decisions d ON ao.decision_id = d.id
JOIN runs r      ON d.run_id = r.id
WHERE ao.agent_name = 'sentiment_analyst'
  AND (ao.raw_output->>'n_headlines')::int > 0
GROUP BY r.backtest_id
ORDER BY r.backtest_id;
