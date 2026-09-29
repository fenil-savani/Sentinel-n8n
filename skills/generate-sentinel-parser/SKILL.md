---
name: generate-sentinel-parser
description: Generate a new Microsoft Sentinel KQL parser (Kusto function YAML) from sample
  data, a schema, an OpenAPI spec, or a Postman collection — and fix, harden, or debug an
  existing custom Sentinel parser (column_ifexists safety, union-with-empty-datatable,
  TimeGenerated fallback, explicit projection, KQL validation errors, Log Ingestion API
  migration). Use this whenever the user wants a new Sentinel parser written, an existing
  parser's KQL debugged or reviewed, or a parser hardened against missing/optional fields —
  including requests phrased as "why is my parser erroring", "harden this KQL function", or
  "update this parser for the Log Ingestion API".
---

# Generate or fix a Microsoft Sentinel parser

A **Microsoft Sentinel parser** is a KQL function (packaged as YAML) that maps a raw custom
log table (`<Product>_<LogType>_CL`) into a normalized, query-friendly view. This skill covers
two entry points into the same artifact:

- **Generating a new parser from scratch** — Steps 1–3 below.
- **Fixing, hardening, or debugging an existing parser** — the checklist in
  "Fixing an existing parser" below, then `reference/troubleshooting.md` for CI-failure
  fixes, the live-test loop, and the full KQL fix snippet library.

KQL reference for anything more complex than what's covered here:
<https://learn.microsoft.com/en-us/kusto/?view=microsoft-fabric>

---

## Step 1 — Collect input from the user

Always get the **custom table name** the DCR writes to (e.g.
`Corelight_v2_asset_classification_CL`) up front — you need it either way, to know what the
parser reads from and, if it already exists, to look its schema up directly instead of asking
for one.

If `get_table_schema` is available to you (it is in the backend generation flow; ask for it
explicitly if you're running interactively with MCP/Azure access wired up) and the table
already exists in the configured Sentinel workspace, **call it with the table name before
asking the analyst for anything else** — it returns the real column names and types straight
from the workspace, which is ground truth and saves them typing out a field list by hand. It
works even if the table has zero rows so far, as long as it's been created (a DCR-based table
exists with its schema from creation; a legacy HTTP Data Collector API table only exists after
its first ingest). A `missing table` error just means the table doesn't exist yet or the name
is wrong — fall through to asking for one of the inputs below instead of treating it as a hard
failure.

If the table doesn't exist yet, `get_table_schema` isn't available, or the analyst is
designing the table alongside the parser, ask for **one** of the following, whichever they
have available (use `AskUserQuestion` if that tool is available to you; otherwise just ask in
the conversation, or call `request_input` if you're running as the backend generation agent):

1. **Sample log data / events** — at least 2-5 representative JSON records covering common and rare field combinations.
2. **Full schema definition** — JSON Schema, Avro, or a documented field list with types and descriptions.
3. **OpenAPI specification** — the response schema for the log-producing endpoint.
4. **Postman collection / export** — the collection JSON with example responses.

Also collect:
- **Product name** (e.g. `Corelight`, `Qualys`, `Wiz`)
- **Log type / category** (e.g. `asset_classification`, `notice`, `vulnerability`)
- **Reference parser** (optional) — an existing parser in the repo to match style/conventions
  (e.g. `reference/corelight_intel.yaml`). If one exists, READ IT FIRST and mirror its structure.

## Step 2 — Validate schema completeness

Before writing anything, verify the user's input contains:

- [ ] All field names
- [ ] Field types (string, int, real, datetime, bool, array, dynamic) or determine by sample data
- [ ] At least one example value per field (optional)
- [ ] Nested-object handling expectations (flatten? keep as dynamic? optional)

If anything is missing, **stop and ask** rather than guessing. Cite the specific missing items.
A schema fetched via `get_table_schema` already satisfies field names and types; it won't give
you example values or tell you what's inside a `dynamic` column — ask for a sample of just
those fields rather than guessing their internal shape.

If the user gave sample data only, derive the schema by scanning every record (not just the
first) and union the keys — fields that appear in *any* record are part of the schema, marked
optional unless they appear in all records. (Write a small Python script to derive this if the
sample set is large, rather than eyeballing every record — it's cheaper on tokens.)

## Step 3 — Generate the parser following these rules

**The output must be a single YAML file** named `<product>_<logtype>.yaml` (e.g.
`corelight_asset_classification.yaml`). Use this structure:

```yaml
id: <new GUID>
Function:
  Title: <Product> <LogType> Events
  Version: '1.0.0'
  LastUpdated: '<today, YYYY-MM-DD>'
Category: Microsoft Sentinel Parser
FunctionName: <product>_<logtype>
FunctionAlias: <product>_<logtype>
FunctionQuery: |
    let dummy_table = datatable(TimeGenerated: datetime, <unique_key_field>: string) [];
    let <product>_<logtype> = view () {
        union isfuzzy=true <CustomTable_CL>, dummy_table
        | summarize arg_max(TimeGenerated, *) by <unique_key_field>   // optional dedup
        | extend
            <normalized_field> = column_ifexists("<raw_column>", <default>),
            ...
        | extend
            EventVendor  = "<Product>",
            EventProduct = "<ProductLine>",
            EventType    = "<logtype>",
            ts           = TimeGenerated,
            ...
        | project
            TimeGenerated,
            <fields in stable order>,
            EventVendor, EventProduct, EventType
    };
    <product>_<logtype>
```

If the source table has multiple physical variants (e.g. a `_red_CL` reduced copy or a
`_long_CL` long-form copy from the same DCR), `union isfuzzy=true` across all of them plus
`dummy_table` — see `reference/corelight_conn.yaml` for a real three-way union.

### Parser rules (MS Sentinel Parser Best Practice)

- **Optional fields** — Every `extend` MUST use `column_ifexists("raw_col_name", <default>)`
  (at initial level only). This is non-negotiable. Defaults: `""` for strings, `real(null)`
  for doubles/ints, `datetime(null)` for datetimes, `dynamic([])` for arrays, `dynamic({})`
  for objects.
- **Union with empty table** — Always start with `union isfuzzy=true <Table>_CL, dummy_table`
  so the function returns an empty result instead of erroring when the table doesn't exist
  yet. Declare `dummy_table` with at minimum `TimeGenerated` and the dedup key column.
- **No hardcoded values** — Never hardcode URLs, credentials, tenant IDs, or
  environment-specific values in the parser.
- **Naming** — `FunctionName` and `FunctionAlias` must be `<product>_<logtype>` (snake_case,
  lowercase). Field names in the output should be snake_case without the `_s`/`_d`/`_t`
  Sentinel suffixes (e.g. raw `ip_s` → normalized `ip`).
- **Column suffix awareness** — Raw columns carry a type suffix: `_s` (string), `_d`
  (double/int), `_t` (datetime), `_b` (bool), `_g` (guid). The raw column passed to
  `column_ifexists` must include the suffix; the normalized alias should not.
- **Nested JSON** — If a field is a JSON object kept as `dynamic`, parse it lazily with
  `parse_json(tostring(...))` only where needed. Don't flatten everything by default — keep
  nested data as `dynamic` and let workbook queries drill in.
- **Standard normalized fields** — When the data supports them, add: `EventVendor`,
  `EventProduct`, `EventType`, `ts`, `src`/`src_ip`/`src_host`/`src_port`,
  `dest`/`dest_ip`/`dest_host`/`dest_port`, `sensor_name`. Skip ones that don't apply — don't
  fabricate them.
- **Dedup** — If the log has a natural unique key (e.g. `uid_s` for connection events, `ip_s`
  for asset events), include `| summarize arg_max(TimeGenerated, *) by <key>` to dedupe.
- **Final project** — End with a `| project` that lists fields in a stable order. This
  prevents downstream queries from breaking if upstream column order changes.
- **UUID (`id`)** — generate once (`python -c "import uuid; print(uuid.uuid4())"`) and never
  change it afterwards; only bump `Function.Version` on updates.

If Log Analytics credentials are configured for this session, validate the query with
`run_kql` before submitting — it catches syntax errors and proves the table/columns you
referenced actually exist (append `| take 1` to just prove validity cheaply). If no
credentials are configured it degrades to a skip, which is fine.

## Fixing an existing parser

Same artifact, different starting point: you're debugging KQL errors, hardening a parser that
was written without the safety rules above, or migrating one to a newer table/column layout.
Run it against this checklist:

1. **All necessary fields parsed** — every field needed downstream is projected.
2. **All fields optional** — `column_ifexists(...)` for every field, so a missing field never
   fails parsing (see the Parser rules above).
3. **Union with empty datatable** — so dashboards don't error when the table is empty.
4. **Explicit projection** — no `project *`; list fields in a stable order (also above).
5. **No static URLs/credentials** anywhere in the parser.
6. **Updated for Log Ingestion API** — if this parser still targets the legacy HTTP Data
   Collector API (EOL 2026-09-14), migrating means updating table/column names (dropping the
   `_s`/`_t`/`_d` suffixes that API imposed) to align with the DCR's `streamDeclarations`
   instead.
7. **Tested against typed sample data** — validate against data containing every field, each
   with its expected type, not just the happy path.

For the live-test loop (deploy → query → fix → redeploy), the exhaustive KQL fix snippet
library (safe field access, `TimeGenerated` fallback, watchlist-based exclusions), CI-failure
diagnosis (e.g. Sentinel's `KS204` schema-validation error), and full metadata/naming rules,
read **`reference/troubleshooting.md`** — it's deliberately kept out of this file so a plain
"generate me a parser" request doesn't have to load debugging material it won't use.

## Reference parsers in this repo

- `reference/corelight_conn.yaml` — connection events; three-way `union isfuzzy=true` across
  table variants, a `datatable` used for enum lookup (`conn_state` → description/action).
- `reference/corelight_intel.yaml` — threat-intel events.

Read one of these first when the user names a similar log source, to match established style.
