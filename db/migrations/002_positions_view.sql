-- 002_positions_view.sql
--
-- `positions` is deliberately a VIEW, not a table. A position is a
-- derived fact — "net effect of open outcomes for a symbol, priced at
-- the latest snapshot" — not something new that needs to be written.
-- Storing it as its own table would mean updating it every time an
-- outcome or a price snapshot changes, and the two copies WILL drift
-- apart eventually. A view computes it fresh on every read instead.
--
-- Read this as two building blocks (CTEs) combined at the end:
--
-- 1. latest_price: for each symbol, the single most recent price
--    snapshot. `DISTINCT ON (symbol) ... ORDER BY symbol, captured_at DESC`
--    is the Postgres idiom for "top 1 row per group" — there's no
--    simpler built-in for this, and it's worth knowing this pattern by
--    heart because it comes up constantly.
--
-- 2. open_outcomes: for each symbol, aggregate every still-open outcome
--    into one net position — total quantity, and a quantity-weighted
--    average entry price (SUM(price * qty) / SUM(qty), not a plain
--    AVG(price), because two lots of different sizes shouldn't count
--    equally toward the average).
--
-- The final SELECT joins those two and computes unrealized P&L as
-- (current price - avg entry price) * quantity — mark-to-market, the
-- standard definition.

CREATE VIEW positions AS
WITH latest_price AS (
    SELECT DISTINCT ON (symbol)
        symbol,
        close AS current_price,
        captured_at
    FROM price_snapshots
    ORDER BY symbol, captured_at DESC
),
open_outcomes AS (
    SELECT
        symbol,
        SUM(quantity) AS quantity,
        SUM(entry_price * quantity) / NULLIF(SUM(quantity), 0) AS avg_entry_price,
        MIN(opened_at) AS opened_at
    FROM outcomes
    WHERE status = 'OPEN'
    GROUP BY symbol
    HAVING SUM(quantity) > 0
)
SELECT
    oo.symbol,
    oo.quantity,
    ROUND(oo.avg_entry_price, 4) AS avg_entry_price,
    lp.current_price,
    ROUND((lp.current_price - oo.avg_entry_price) * oo.quantity, 4) AS unrealized_pnl,
    oo.opened_at
FROM open_outcomes oo
LEFT JOIN latest_price lp ON lp.symbol = oo.symbol;
