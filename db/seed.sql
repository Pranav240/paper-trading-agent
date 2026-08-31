-- Not a migration — seed data, run manually when you want a fresh
-- database to actually be usable. `decisions.symbol` and
-- `outcomes.symbol` are foreign keys into `watchlist`, so POST
-- /run/trigger will fail with a foreign key violation until at least
-- one symbol exists here. Kept separate from db/migrations/ on purpose:
-- migrations define structure, seeding is deciding what data to start
-- with — different concerns, so they don't belong in the same files.

INSERT INTO watchlist (symbol) VALUES ('AAPL')
ON CONFLICT (symbol) DO NOTHING;
