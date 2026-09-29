# Parser troubleshooting reference

Read this when you're **fixing, hardening, or debugging** an existing parser, not when
generating a new one from scratch (that workflow lives in `SKILL.md` itself, since it's the
frequently-needed path).

A note on scope before you use anything below: some of this material describes tooling and a
repo layout from the broader Azure-Sentinel community-solution ecosystem, which this specific
repo does not fully implement yet. Each section says explicitly what's actually wired up here
versus what's a forward reference — check that before assuming a tool or path exists.

---

## Verify and live-test on Azure

**What actually exists in this repo:** the `run_kql` tool — run an ad-hoc KQL query against
the configured Log Analytics workspace and see whether it parsed and how many rows came back
(append `| take 1` to prove validity cheaply without pulling real rows). If no Log Analytics
credentials are configured, it returns `SKIPPED` rather than failing, so writing/fixing a
parser is never blocked on having live Azure access — the same post-generation lint still
gates deployment either way.

Dev loop with what's available here:
1. Fix the KQL in the parser YAML.
2. Run the fixed query body through `run_kql` (swap `<product>_<logtype>` view syntax for a
   plain query over the same `union` — `run_kql` executes raw KQL, not the packaged function).
3. A KQL error in the result ⇒ almost always a missing `column_ifexists`, a schema mismatch
   (wrong raw column name/suffix), or a missing `union` empty-table branch. Fix, re-run.
4. Once `run_kql` is clean, submit/redeploy and confirm rows land with the expected typed
   columns via the Sentinel/Log Analytics portal.

**Forward references (not present in this repo's MCP setup today):** some Sentinel-tooling
setups expose a richer toolset for this same loop — `run_one_check("check_kql", <solution>)`
for a slower repo-wide KQL validation pass, `run_one_check("check_non_ascii", <solution>)`,
`test_parser(parser_name, limit=10)` to deploy-and-query a named parser directly, and
`run_kql_query`/`export_kql_query` for typed sample-data pulls. If a future MCP integration
adds these, prefer them over the manual `run_kql` loop above — they close the same gap with
less manual query-rewriting. Until then, treat any instruction referencing them as
aspirational and fall back to the loop above.

## Common CI failures → fix

This section assumes the parser also lives in (or will be submitted to) a full
Azure-Sentinel **community solution repo** with its standard CI layout
(`.script/tests/kqlvalidationtests/...`) — this repo itself does not contain that layout
(it deploys generated parsers via the `sentinel-agent` FastAPI service + n8n's `deploy_draft`
tool instead, straight to a live workspace, not through that CI pipeline). If you're only
working within this repo, this section is informational for when the same parser gets
packaged into that ecosystem later; skip the file path below if it doesn't exist here.

**KQL Validation `KS204`** — e.g. *"The name 'ContrastADRIncidents_CL' does not refer to any
known table, tabular variable or function. Code: 'KS204', Severity: 'Error'"*.
Cause: the custom table's schema isn't registered, so the validator doesn't know the table
used in the parser / analytic rule / workbook.
Fix: add a schema file at **`.script/tests/kqlvalidationtests/CustomTables/<Table>_CL.json`**
using the **exact** table name and **all required schema fields** — see a packaging skill for
the JSON shape if/when one exists in your setup (`sentinel-packaging` is referenced as a
planned specialist skill in this repo's orchestrator prompt, but doesn't exist as a skill file
here yet).

## KQL fixes — snippet library

### Safe field access (all fields optional)
```kql
| extend EventType = tostring(column_ifexists("type", ""))      // not: = type
| extend EventTime = todatetime(column_ifexists("time", ""))
| extend EventCount = toint(column_ifexists("count", 0))
| extend RawDetails = todynamic(column_ifexists("details", "{}"))
| extend Severity = coalesce(tostring(column_ifexists("severity","")), tostring(column_ifexists("level","")), "Informational")
```

### TimeGenerated coalesce fallback
```kql
| extend TimeGenerated = coalesce(todatetime(column_ifexists("EventTime","")), todatetime(column_ifexists("time","")), now())
```

### Union with empty datatable (consistent schema when no data)
```kql
| project TimeGenerated, EventType, SrcIp
| union (datatable(TimeGenerated:datetime, EventType:string, SrcIp:string)[])
```

### Explicit projection (no `project *`); always include `Type`. No hardcoded values:
```kql
| where SrcIp !in (toscalar(_GetWatchlist('ExcludedIPs') | project SearchKey))   // not literal IPs
| where TimeGenerated > ago(90d)                                                  // not datetime(2024-01-01)
```

- Align column names exactly with the ARM template's `streamDeclarations`; drop the
  `_s`/`_t`/`_d` suffixes (those are an HTTP Data Collector API artifact — the Log Ingestion
  API doesn't impose them).
- Use lookup `datatable`s for enum mapping (see `reference/corelight_conn.yaml`'s `conn_state`
  lookup for a real example); `join` reference parsers for cross-table context.

## Metadata rules

- **UUID** (`id`): generate once, never change; only bump `Function.Version` on updates.
- **FunctionName/Alias**: `<vendor>_<logtype>` (e.g. `corelight_http`); keep it consistent
  across every file for the same source.
- **Category**: always `Microsoft Sentinel Parser`.
- Document any renamed reserved keyword (e.g. `type` → `DetectionType`, `time` → `EventTime`)
  so the rename is traceable back to the raw field, and avoid circular parser references
  (parser A calling parser B calling parser A).

## Related specialist skills

These are referenced by name in this repo's n8n orchestrator prompt as planned follow-on
specialists for adjacent parts of a Sentinel solution, but none of them exist as a skill file
under `skills/` yet — treat any mention of them (here or elsewhere) as a forward reference,
not a callable tool:

- `sentinel-data-connector` / `sentinel-ccf-connector` — the ingestion side (Azure Function or
  Codeless Connector Framework) that fills the `_CL` table this parser reads from.
- `sentinel-packaging` — publisher/offer metadata, `mainTemplate` ARM assembly, the
  `kqlvalidationtests` schema files referenced above.
- `sentinel-asim-parser` / `sentinel-azure-devtest` — not referenced anywhere else in this
  repo either; if you're pointed at a parser under `ASIM/`/`im*`/`vim*` (ASIM-normalized,
  different YAML and query model from the custom parsers this skill covers), there is
  currently no dedicated skill for it here.
