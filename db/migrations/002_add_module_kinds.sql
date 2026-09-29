-- Widens `drafts.kind` and `deployments.resource_type` for the new generator
-- modules (analytic rules and TDD now; playbook/ccf_connector/asim_parser/
-- packaging land in later passes but are included here so this migration
-- only needs to run once). `db/init.sql` only runs on a fresh Postgres
-- volume, so an existing deployment must apply this by hand:
--
--   docker compose exec -T postgres psql -U "$POSTGRES_USER" -d sentinel < db/migrations/002_add_module_kinds.sql
--
-- Idempotent: safe to re-run.

\connect sentinel

ALTER TABLE drafts DROP CONSTRAINT IF EXISTS drafts_kind_check;
ALTER TABLE drafts ADD CONSTRAINT drafts_kind_check
    CHECK (kind IN ('parser', 'workbook', 'analytic_rule', 'playbook',
                    'ccf_connector', 'asim_parser', 'tdd', 'packaging'));

ALTER TABLE deployments DROP CONSTRAINT IF EXISTS deployments_resource_type_check;
ALTER TABLE deployments ADD CONSTRAINT deployments_resource_type_check
    CHECK (resource_type IN ('savedSearch', 'workbook', 'alertRule', 'templateDeployment'));
