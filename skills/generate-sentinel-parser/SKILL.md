---
name: Generate Sentinel Parser
description: Generate a Microsoft Sentinel KQL parser (Kusto function YAML) from sample data, schema, OpenAPI spec, or Postman collection — following Sentinel parser best practices.
---

# Generate a Microsoft Sentinel Parser

You are generating a **Microsoft Sentinel KQL parser** (a Kusto function YAML) that maps a raw custom log table (`<Product>_<LogType>_CL`) into a normalized, query-friendly view. The output must be production-ready and follow the rules below verbatim.

KQL reference if query required more complex functions: <https://learn.microsoft.com/en-us/kusto/?view=microsoft-fabric>

---

## Step 1 — Collect input from the user

Ask the user for **one** of the following, whichever they have available. Use `AskUserQuestion` if they haven't already provided it:

1. **Sample log data / events** — at least 2-5 representative JSON records covering common and rare field combinations.
2. **Full schema definition** — JSON Schema, Avro, or a documented field list with types and descriptions.
3. **OpenAPI specification** — the response schema for the log-producing endpoint.
4. **Postman collection / export** — the collection JSON with example responses.

Also collect:
- **Product name** (e.g. `Corelight`, `Qualys`, `Wiz`)
- **Log type / category** (e.g. `asset_classification`, `notice`, `vulnerability`)
- **Custom table name** the DCR writes to (e.g. `Corelight_v2_asset_classification_CL`)
- **Reference parser** (optional) — an existing parser in the repo to match style/conventions (e.g. `Corelight/corelight_intel.yaml`). If one exists, READ IT FIRST and mirror its structure.

## Step 2 — Validate schema completeness

Before writing anything, verify the user's input contains:

- [ ] All field names
- [ ] Field types (string, int, real, datetime, bool, array, dynamic) or determine by sample data
- [ ] At least one example value per field  (Optional)
- [ ] Nested-object handling expectations (flatten? keep as dynamic? Optional)

If anything is missing, **stop and ask** rather than guessing. Cite the specific missing items.

If the user gave sample data only, derive the schema by scanning every record (not just the first) and union the keys — fields that appear in *any* record are part of the schema, marked optional unless they appear in all records. (create python script to derive if possible to reduce token)

## Step 3 — Generate the parser following these rules

**The output must be in a single YAML file** named `<product>_<logtype>.yaml` (e.g. `corelight_asset_classification.yaml`). Use this structure:

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

### Parser rules (MS Sentinel Parser Best Practice)

- **3.3 Optional fields** — Every `extend` MUST use `column_ifexists("raw_col_name", <default>)`(At initial level only). This is non-negotiable. Defaults: `""` for strings, `real(null)` for doubles/ints, `datetime(null)` for datetimes, `dynamic([])` for arrays, `dynamic({})` for objects.
- **3.5 Union with empty table** — Always start with `union isfuzzy=true <Table>_CL, dummy_table` so the function returns an empty result instead of erroring when the table doesn't exist yet. Declare `dummy_table` with at minimum `TimeGenerated` and the dedup key column.
- **3.6 No hardcoded values** — Never hardcode URLs, credentials, tenant IDs, or environment-specific values in the parser.
- **Naming** — `FunctionName` and `FunctionAlias` must be `<product>_<logtype>` (snake_case, lowercase). Field names in the output should be snake_case without the `_s`/`_d`/`_t` Sentinel suffixes (e.g. raw `ip_s` → normalized `ip`).
- **column suffix awareness** — If data have  `_s` (string), `_d` (double/int), `_t` (datetime), `_b` (bool), `_g` (guid) to custom column names. The raw column passed to `column_ifexists` must include the suffix; the normalized alias should not.
- **Nested JSON** — If a field is a JSON object kept as `dynamic`, parse it lazily with `parse_json(tostring(...))` only where needed. Don't flatten everything by default — keep nested data as `dynamic` and let workbook queries drill in.
- **Standard normalized fields** — When the data supports them, add: `EventVendor`, `EventProduct`, `EventType`, `ts`, `src`/`src_ip`/`src_host`/`src_port`, `dest`/`dest_ip`/`dest_host`/`dest_port`, `sensor_name`. Skip ones that don't apply — don't fabricate them.
- **Dedup** — If the log has a natural unique key (e.g. `uid_s` for connection events, `ip_s` for asset events), include `| summarize arg_max(TimeGenerated, *) by <key>` to dedupe.
- **Final project** — End with a `| project` that lists fields in a stable order. This prevents downstream queries from breaking if upstream column order changes.

## Reference patterns 

- Corelight Connection parser - `data/corelight_conn.yaml`
- Corelight Intel parser - `data/corelight_intel.yaml` 
