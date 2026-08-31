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

from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.models import Action, Decision, Position, RunResult, RunStatus


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
        """Run one (still stubbed) agent cycle, but for real this time:
        it writes a `runs` row, a `decisions` row, and four
        `agent_opinions` rows inside a single transaction, so a failure
        partway through leaves no partial data behind.

        Phase 03 replaces the hardcoded values below with actual
        LangGraph agent output. The shape being written — one decision,
        several opinions feeding it — doesn't change; only where the
        values come from does.
        """
        started_at = datetime.now(timezone.utc)

        async with self._pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        "INSERT INTO runs (status, started_at) "
                        "VALUES ('RUNNING', %s) RETURNING id",
                        (started_at,),
                    )
                    run_row = await cur.fetchone()
                    run_id = run_row["id"]

                    technicals_snapshot = {"rsi_14": 51.0, "sma_20": 191.40}
                    sentiment_snapshot = {"headline_score": 0.02, "n_headlines": 1}

                    await cur.execute(
                        """
                        INSERT INTO decisions
                            (run_id, symbol, action, confidence, reasoning,
                             technicals_snapshot, sentiment_snapshot)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING id, symbol, action, confidence, reasoning,
                                  technicals_snapshot, sentiment_snapshot, created_at
                        """,
                        (
                            run_id,
                            "AAPL",
                            Action.HOLD.value,
                            0.55,
                            "Stub run — Phase 03 will replace this with a real "
                            "LangGraph decision flow.",
                            psycopg.types.json.Json(technicals_snapshot),
                            psycopg.types.json.Json(sentiment_snapshot),
                        ),
                    )
                    decision_row = await cur.fetchone()
                    decision = Decision(**decision_row)

                    opinions = [
                        ("technical_analyst", "HOLD", 0.55, "RSI near neutral, no clear edge."),
                        ("sentiment_analyst", "HOLD", 0.50, "Sparse headlines, low signal."),
                        ("risk_manager", "APPROVE", None, "Within all position/loss limits."),
                        ("portfolio_manager", "HOLD", 0.55, "No specialist showed conviction."),
                    ]
                    for agent_name, opinion, confidence, reasoning in opinions:
                        await cur.execute(
                            """
                            INSERT INTO agent_opinions
                                (decision_id, agent_name, opinion, confidence, reasoning)
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            (decision.id, agent_name, opinion, confidence, reasoning),
                        )

                    finished_at = datetime.now(timezone.utc)
                    await cur.execute(
                        "UPDATE runs SET status = 'SUCCESS', finished_at = %s WHERE id = %s",
                        (finished_at, run_id),
                    )

        return RunResult(
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            status=RunStatus.SUCCESS,
            decisions=[decision],
        )

