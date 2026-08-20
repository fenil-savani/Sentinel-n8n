-- Runs once, on first initialisation of an empty Postgres data directory.
-- Creates the `sentinel` database alongside n8n's, then its schema.
--
-- To re-run against an existing volume:
--   docker compose exec -T postgres psql -U "$POSTGRES_USER" -d sentinel < db/init.sql
-- (the CREATE DATABASE will error harmlessly; everything else is idempotent)

CREATE DATABASE sentinel;

\connect sentinel

-- ── drafts ──────────────────────────────────────────────────────────────
-- The artifact store. Generated parsers/workbooks live here and NEVER pass
-- through LLM context; the orchestrator only ever handles `id` + `summary`.
CREATE TABLE IF NOT EXISTS drafts (
    id            TEXT PRIMARY KEY,                 -- drf_<ulid>
    kind          TEXT NOT NULL
                  CHECK (kind IN ('parser', 'workbook')),
    name          TEXT NOT NULL,                    -- functionAlias, or workbook displayName
    -- needs_input is a pause, not a failure: the skill's "STOP and ask rather
    -- than guessing" rule fired and the analyst owes an answer. A workbook
    -- with some failed panels is still 'validated', with the misses named in
    -- `failures` below — there is no separate deploy-gated status for it.
    status        TEXT NOT NULL DEFAULT 'generating'
                  CHECK (status IN ('generating', 'needs_input', 'validated',
                                    'failed', 'deployed')),

    -- The full artifact. YAML text for parsers, JSON text for workbooks.
    -- Deliberately TEXT, not JSONB: a workbook's serializedData is a string
    -- and byte-for-byte fidelity matters on redeploy diffs.
    artifact      TEXT,

    -- Small, LLM-safe projection returned by /drafts/{id}/summary.
    summary       JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- {kql_ok, kql_failed, lint, findings[]}
    validation    JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Per-panel failures on a workbook draft, if any: [{id, title, error}]
    failures      JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- Chat session that produced it, so an analyst can list their own drafts.
    session_id    TEXT,

    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS drafts_session_idx ON drafts (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS drafts_status_idx  ON drafts (status);

-- ── deployments ─────────────────────────────────────────────────────────
-- One row per deploy attempt, written once the PUT has already happened —
-- there is no separate preflight phase, and no rollback: a bad deploy is
-- corrected by fixing the draft and redeploying, not by an automated undo.
CREATE TABLE IF NOT EXISTS deployments (
    id              TEXT PRIMARY KEY,               -- dep_<ulid>
    draft_id        TEXT NOT NULL REFERENCES drafts (id) ON DELETE RESTRICT,

    resource_type   TEXT NOT NULL
                    CHECK (resource_type IN ('savedSearch', 'workbook')),
    resource_id     TEXT NOT NULL,                  -- full ARM resource id

    action          TEXT NOT NULL CHECK (action IN ('create', 'update')),

    request_body    JSONB,
    response_body   JSONB,
    http_status     INTEGER,

    -- 'unknown' covers a timed-out PUT with no response: PUT is idempotent
    -- by resource name, so a retry is safe, but the caller should GET the
    -- resource to confirm before assuming either outcome.
    status          TEXT NOT NULL
                    CHECK (status IN ('deployed', 'failed', 'unknown')),
    error           TEXT,

    deployed_by     TEXT,                           -- chat sessionId
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS deployments_draft_idx    ON deployments (draft_id);
CREATE INDEX IF NOT EXISTS deployments_resource_idx ON deployments (resource_id, created_at DESC);

-- ── updated_at maintenance ──────────────────────────────────────────────
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS drafts_touch ON drafts;
CREATE TRIGGER drafts_touch BEFORE UPDATE ON drafts
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

DROP TRIGGER IF EXISTS deployments_touch ON deployments;
CREATE TRIGGER deployments_touch BEFORE UPDATE ON deployments
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
