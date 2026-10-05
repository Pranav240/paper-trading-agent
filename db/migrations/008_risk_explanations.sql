-- 008_risk_explanations.sql
--
-- One row per explanation the explainer node (V2, phase 08) writes for a
-- risk decision: why the VaR budget scaled or vetoed a trade, grounded in
-- the news around the losses that VaR figure is built from.
--
-- Stored with everything needed to audit it later (phase 09 grades these,
-- phase 10 traces them): which headlines were retrieved and how, which it
-- cited, whether every citation was actually among the retrieved ones, and
-- the deterministic no-news template it has to beat.
--
-- `citations_valid` is computed by the node, not trusted from the model:
-- an explanation citing a headline it was never shown is invented, and is
-- kept (so the failure rate is measurable) but flagged.

CREATE TABLE risk_explanations (
    id                      BIGSERIAL PRIMARY KEY,
    decision_id             BIGINT NOT NULL UNIQUE REFERENCES decisions(id) ON DELETE CASCADE,
    symbol                  TEXT NOT NULL,
    as_of                   TIMESTAMPTZ NOT NULL,
    trigger_flags           TEXT[] NOT NULL,
    model                   TEXT NOT NULL,
    prompt_version          TEXT NOT NULL,
    retrieval_method        TEXT NOT NULL,
    -- {"<driver date>": [headline ids, best first]} as retrieved
    retrieved               JSONB NOT NULL,
    cited_headline_ids      BIGINT[] NOT NULL DEFAULT '{}',
    citations_valid         BOOLEAN NOT NULL,
    validation_problems     TEXT[] NOT NULL DEFAULT '{}',
    summary                 TEXT NOT NULL,
    drivers                 JSONB NOT NULL,
    template_baseline       TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_risk_explanations_as_of ON risk_explanations (symbol, as_of);
