"""
Postgres-backed implementation of the Repository protocol.

This is the only file that knows any SQL. Everything above it (routers,
Pydantic models) stays exactly as it was in Phase 01 — proving the
get_store() seam actually worked.

Notes on the SQL itself:

- Every value that comes from the caller (symbol, limit) goes in as a
  `%s` placeholder, never string-interpolated into the query text. This
  is the actual defense against SQL injection — psycopg sends the query
  and the parameters to Postgres separately, so user input can never be
  interpreted as part of the SQL syntax, no matter what characters it
  contains.
- `row_factory=dict_row` makes each result row come back as a dict
  (column name -> value) instead of a plain tuple, so `Position(**row)`
  can build a Pydantic model directly from it — the row's column names
  have to match the model's field names for this to work, which is a
  reason to keep migration column names aligned with the Pydantic models.
- `async with self._pool.connection() as conn` borrows a connection from
  the pool for exactly the duration of the block and returns it
  afterward — you never manually track open/close.
"""

from __future__ import annotations

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.agent.data_sources import build_live_data_sources
from app.agent.graph import build_decision_graph
from app.agent.runner import run_decision_cycle
from app.models import Decision, Position, RunResult


class PostgresRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def list_positions(self, symbol: str | None = None) -> list[Position]:
        query = "SELECT * FROM positions"
        params: list[object] = []
        if symbol:
            query += " WHERE symbol = %s"
            params.append(symbol.upper())

        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()

        return [Position(**row) for row in rows]

    async def list_decisions(
        self, symbol: str | None = None, limit: int = 50
    ) -> list[Decision]:
        query = """
            SELECT id, symbol, action, confidence, reasoning,
                   technicals_snapshot, sentiment_snapshot, created_at
            FROM decisions
        """
        params: list[object] = []
        if symbol:
            query += " WHERE symbol = %s"
            params.append(symbol.upper())
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()

        return [Decision(**row) for row in rows]

    async def trigger_run(self) -> RunResult:
        """Runs one real multi-agent decision cycle (Phase 03) across every
        active watchlist symbol and persists it.

        This method is now thin on purpose: building the graph and doing
        the actual work lives in app/agent/graph.py and
        app/agent/runner.py, which is what makes those testable without a
        FastAPI app or a live Postgres pool. Rebuilding the graph (and its
        two ChatOpenAI clients) on every call is a known simplification —
        cheap enough for a once-a-day V1 cycle, worth revisiting only when
        Phase 07's continuous-intraday loop makes per-call setup cost
        matter.
        """
        price_source, headline_source = build_live_data_sources()
        graph = build_decision_graph(
            price_source=price_source,
            headline_source=headline_source,
            repository=self,
        )
        return await run_decision_cycle(self._pool, graph)

