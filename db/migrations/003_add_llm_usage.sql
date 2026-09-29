-- New table only — no ALTER of drafts/deployments. One row per LLM call:
-- one per AnthropicRuntime tool-loop iteration, or one per ClaudeCliRuntime
-- subprocess invocation (whose own internal multi-turning is opaque to us
-- and reported as a single row). Keyed by run_id, minted fresh per
-- generate/revise call (a revision gets its own run_id against the same
-- draft_id, so per-attempt cost stays distinguishable from lifetime draft
-- cost) so a run that fails before producing a usable artifact still
-- produces billing rows. `draft_id` is filled in from the moment the draft
-- row exists (which today is before any LLM call in every generator), and
-- nullable only so history survives if a draft is ever purged.
--
-- `db/init.sql` only runs on a fresh Postgres volume, so an existing
-- deployment must apply this by hand:
--
--   docker compose exec -T postgres psql -U "$POSTGRES_USER" -d sentinel < db/migrations/003_add_llm_usage.sql
--
-- Idempotent: safe to re-run.

\connect sentinel

CREATE TABLE IF NOT EXISTS llm_usage (
    id                          TEXT PRIMARY KEY,        -- llu_<ulid>
    run_id                      TEXT NOT NULL,
    draft_id                    TEXT REFERENCES drafts (id) ON DELETE SET NULL,
    session_id                  TEXT,

    provider                    TEXT NOT NULL
                                CHECK (provider IN ('anthropic', 'claude_cli')),
    model                       TEXT NOT NULL,
    call_site                   TEXT NOT NULL,           -- 'parser' | 'analytic_rule' | 'tdd' |
                                                          -- 'workbook_manifest' | 'workbook_panel'
    iteration                   INTEGER NOT NULL DEFAULT 1,

    input_tokens                INTEGER NOT NULL DEFAULT 0,
    output_tokens               INTEGER NOT NULL DEFAULT 0,
    cache_creation_5m_tokens    INTEGER NOT NULL DEFAULT 0,
    cache_creation_1h_tokens    INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens           INTEGER NOT NULL DEFAULT 0,

    cost_usd                    NUMERIC(12, 6),          -- NULL when the model is unpriced
    provider_reported_cost_usd  NUMERIC(12, 6),          -- CLI's own total_cost_usd, cross-check only
    latency_ms                  INTEGER,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Insert-only — no updated_at/touch trigger, unlike drafts/deployments.
CREATE INDEX IF NOT EXISTS llm_usage_run_idx     ON llm_usage (run_id, created_at);
CREATE INDEX IF NOT EXISTS llm_usage_draft_idx   ON llm_usage (draft_id);
CREATE INDEX IF NOT EXISTS llm_usage_session_idx ON llm_usage (session_id, created_at DESC);
