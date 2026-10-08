-- 009_trace_steps.sql
--
-- Phase 10 (docs/phase10-plan.md): the full trace behind a decision --
-- every LLM call (exact prompt, raw response, model, tokens, time), every
-- headline retrieval (window, cutoff, query, each result with rank and
-- score), the risk node's VaR arithmetic and the explainer's citation
-- check -- so a decision can be audited from stored data alone.
--
-- Written in the same transaction as the decision it belongs to (see
-- app/agent/trace.py), so no decision exists without its trace. Rows are
-- append-only facts about what happened; nothing reads them to make a
-- decision.
--
-- input/output are JSONB because their shape differs by kind; the fields
-- every audit needs (node, kind, model, tokens, timing) are columns.

CREATE TABLE trace_steps (
    id              BIGSERIAL PRIMARY KEY,
    decision_id     BIGINT NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    step            INTEGER NOT NULL CHECK (step >= 0),
    parent_step     INTEGER,
    node            TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('llm', 'retrieval', 'check', 'rule')),
    input           JSONB NOT NULL DEFAULT '{}'::jsonb,
    output          JSONB NOT NULL DEFAULT '{}'::jsonb,
    model           TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    started_at      TIMESTAMPTZ NOT NULL,
    duration_ms     DOUBLE PRECISION,
    error           TEXT,
    UNIQUE (decision_id, step)
);

CREATE INDEX idx_trace_steps_decision ON trace_steps (decision_id, step);
