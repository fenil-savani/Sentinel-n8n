---
name: generate-sentinel-workbook
description: Generate a Microsoft Sentinel workbook JSON from a parser and optional reference dashboard (LogScale YAML, Splunk XML, screenshot, or spec) — following Sentinel workbook best practices. Also supports migrating an existing third-party dashboard (Splunk, Falcon, Elastic, etc.) to Sentinel.
---

# Generate a Microsoft Sentinel Workbook

You are generating a **Microsoft Sentinel workbook** (Application Insights Workbook JSON) that visualises data from an existing Sentinel parser. The output must be production-ready, JSON-valid, and follow every rule below.

---
KQL reference if query required more complex functions: <https://learn.microsoft.com/en-us/kusto/?view=microsoft-fabric>

## Step 1 — Determine mode

First, ask the user which mode they want via `AskUserQuestion` or determine directly from user prompt:

- **Generate** — create a new workbook from scratch (from parser fields, panel name list, or a reference dashboard)
- **Replica from Existing Third Party Platform Dashboard** — convert an existing third-party dashboard (Splunk, Falcon LogScale, Elastic, Humio, etc.) to a Sentinel workbook

Then collect the inputs below based on the chosen mode.

---

### Mode A — Generate

Collect (use `AskUserQuestion` for anything not already provided or passed as a command argument):

- **Parser** — existing parser file (e.g. `corelight_conn.yaml`, `google_secops_conn.yaml`) or generate one first via `/generate-sentinel-parser`. The workbook MUST query the parser, If not parser then raw table with `*_CL` suffix.
- **Panel names / spec** *(optional, can be passed as command argument)* — if the user provides a list of panel names (e.g. `"Top IPs, Severity Over Time, Asset Inventory"`) proceed directly to generating those panels without asking for a reference dashboard.
- **Reference dashboard** *(optional, skip if panel list is provided)* — one of:
  - LogScale / Humio dashboard YAML
  - Splunk dashboard XML
  - Screenshot of an existing dashboard
  - Written panel-by-panel spec
  - "No reference — invent panels from the parser's fields"
- **Sample data / events** — at least one record so KQL types can be inferred.
- **Workbook scope** — single-tab or multi-tab (e.g. one tab per log type).

---

### Mode B — Replicate

Collect:

- **Third-party dashboard file** — the source dashboard to migrate. Accepted formats:
  - Splunk: Simple XML dashboard (`.xml`)
  - Falcon / LogScale / Humio: dashboard YAML (`.yaml` / `.yml`)
  - Elastic / Kibana: exported JSON (`.ndjson` or `.json`)
  - Any other platform: screenshot or written spec
- **Parser** — the Sentinel parser that maps the equivalent data. If none exists, offer to run `/generate-sentinel-parser` first.
- **Sample data / events** — at least one record to validate field mappings.
- **Platform name** — e.g. `Splunk`, `CrowdStrike Falcon`, `Elastic`, `Humio`.

Once the source file is provided:
1. READ the file and list every panel (title, viz type, query/SPL/EQL, filters).
2. Map each source query field to the equivalent parser field. Flag any fields with no clear mapping and ask the user before proceeding.
3. Translate SPL / EQL / LogScale queries to KQL equivalents using the parser as the base table.
4. Preserve the original panel layout, title, and intent as closely as possible.
5. Do not consider query-lines which are not being used

---

### Multi-parser workbooks (several log types, one workbook)

When more than one parser is in scope (e.g. "one workbook covering Account, Detection, and
Lockdown data"), the harness passes you all of them. Two rules change:

1. **Every panel in your `submit_manifest` plan must set its own `"parser"` field** to exactly
   one of the parsers you were given — never assume a single default. A panel whose data comes
   from `vectra_ai_detection_events` must say so explicitly, even if another panel in the same
   plan uses `vectra_ai_account_entities`.
2. **Use one tab per parser** (the "Workbook scope" question in Step 1 — this is exactly the
   multi-tab case). The tab switcher IS the "which data type am I looking at" filter; do not also
   try to build a Step 4 per-dimension multiselect that reads from more than one parser — the
   schemas differ, and a shared filter field usually doesn't exist on all of them. Skip Step 4's
   per-dimension filters entirely in multi-parser mode (GlobalTimeRestriction still applies to
   every panel); a panel that needs its own dimension filter can still write it directly into its
   own KQL body, scoped to its own parser's real fields.

Everything else below (visualisation rules, naming, template-safety) applies per panel exactly
as written, just against that panel's own declared parser instead of one shared one.

## Step 2 — Validate inputs

Before generating, confirm:

- [ ] The parser exists and is readable — READ it to know which fields it exposes
- [ ] For **Generate**: panel names or reference is understood (title, viz type, fields, filters, layout)
- [ ] For **Migrate**: every source panel has a KQL equivalent query drafted; unmapped fields are resolved
- [ ] For nested fields (JSON arrays kept as strings), you'll `parse_json` + `mv-expand` at query time

If anything is ambiguous, STOP and ask. Don't fabricate panels or field mappings.

---

## Step 3 — Structural template

Every workbook *must follows this outer skeleton*:

```json
{
  "version": "Notebook/1.0",
  "items": [
    { "type": 1, "content": { "json": "# <Title>\n---\n>**NOTE:** ..." }, "name": "text - header" },
    { "type": 9, "content": { "version": "KqlParameterItem/1.0", "parameters": [ ... ], "style": "pills" }, "name": "parameters - filters" },
    {
      "type": 12,
      "content": {
        "version": "NotebookGroup/1.0",
        "groupType": "editable",
        "title": "<Workbook Title>",
        "items": [
          /* all panels and sub-groups go here */
        ]
      },
      "name": "group_main"
    },
    { "type": 1, "content": { "json": "Refresh the web page to fetch details of recently collected events" }, "name": "text - footer" }
  ],
  "fromTemplateId": "sentinel-<Product>_<Topic>_Dashboard",
  "$schema": "https://github.com/Microsoft/Application-Insights-Workbooks/blob/master/schema/workbook.json"
}
```

Wrap every panel inside ONE outer group (`type: 12`). Use inner `type: 12` sub-groups to organise sections (Overview, Distribution, Trends, Detail Tables). Don't put bare panels at the top level — keep them grouped.

Read `reference/CorelightDataExplorer.yaml` this example dashboard to understand how we create sentinel dashboard.

## Step 4 — Parameters block

Always include these in order:

1. **GlobalTimeRestriction** (type 4) — with the canonical 15-row `selectableValues` list from 5 min to 90 days. Default 1 day (`86400000`).
2. **Per-dimension filters** — one multi-select per logical filter the user cares about (OSName, TypeGroup, TypeName, Severity, etc.). Each populated from the parser.
3. **Free-text filters last** — IP address, hostname, UID — type 1 (text), default `*`.
- Use `isnotempty()` to remove empty data
- Apply parameter to each and every panels (Take reference from provided dashboard how to apply)

## Step 5 — Wire every panel's query

Every panel query starts with this preamble:

```kql
<parser_name>
| where TimeGenerated {GlobalTimeRestriction}
| where ('*' in ({Sensor}) or sensor_name in ({Sensor}))
| where ('*' in ({OSName}) or os_name in ({OSName}))      // for each multi-select filter
| where ('*' == '{IPAddress}' or ip == '{IPAddress}')      // for each text filter
```

DRY principle: if you find yourself writing complex repeated logic across panels, push it into the parser instead — never copy-paste a 20-line transform into 8 panel queries.

## Step 6 — Per-visualisation rules

### KPI tile (`visualization: "tiles"`)

- One tile per result row (the tiles viz auto-renders one tile per row)
- `titleContent.columnMatch` = the label column (e.g. `type_group`)
- `leftContent.columnMatch` = the numeric column (e.g. `Count`, `Unique Devices`)
- `leftContent.formatter: 12` with palette for color
- `numberFormat.unit: 17` (count) — never display raw decimals like `1234.567890`
- Value must be displayed at the center of the panel with appropriate font size
- Add a unit or label below the value so it is self-explanatory
- Set `styleSettings.showBorder: true` — tile panels MUST have a border
- For very large numbers, use compact unit so 1,500,000 renders as `1.5M`
- Verify the highest possible value that can appear still renders correctly without overflow or truncation


### Pie chart (`visualization: "piechart"`)

- Display only top 10 categories; club the remainder under an **"Others"** label:
  ```kql
  | top 11 by Count desc
  | extend Bucket = iff(row_number() > 10, "Others", Category)
  | summarize Count = sum(Count) by Bucket
  ```
- Or simpler: `| top 10 by Count desc` (drops Others silently — only if data tail is negligible)
- Always `chartSettings.showLegend: true` — no legend entry should be skipped
- Sort descending by the numeric column so legend ordering matches slice ordering

### Bar chart (`visualization: "barchart"`)

- Include a label against each bar — labels must be readable and not truncated
- Set `xAxis` to the category, `yAxis` to the metric, optionally `group` for stacked
- Use `createOtherGroup: 10` to club beyond top 10 series
- `showLegend: true` — no legend should be skipped for any bar
- Bar chart should have a border

### Line / time chart (`visualization: "timechart"` or `"areachart"`)

- `xAxis: "TimeGenerated"` (source values on X-axis), `yAxis: ["<metric>"]` (average/aggregate values on Y-axis) — do not swap axes
- For multi-series, use `top-nested 10 of <series> by sum(<metric>)` to enforce the ≤100 series / ≤10,000 points limit
- `showLegend: true`, `createOtherGroup: 10` — legends must not be truncated and no series legend should be skipped
- For "bytes" / large metric values: `ySettings.numberFormatSettings.unit: 1` (bytes) or `17` (count)
- Time chart should have a border

### Grid / table (no `visualization` key, default grid)

Every grid panel MUST have:
- `gridSettings.filter: true` — Search filter must work across all columns of the grid
- `gridSettings.rowLimit: 10000` — show the latest top 10,000 entries without pagination
- `showExportToExcel: true`
- `showRefreshButton: true`
- Default sort descending on the most-relevant time/severity column (use `| sort by ts desc` in the query)
- `labelSettings` array renaming columns to human-readable labels
- For status/severity columns, use `formatters` with `thresholdsGrid` to color-code (High=green, Medium=orange, Low=red, Default=gray)
- If the query could return more than 10,000 rows, add a markdown warning above the grid: `"⚠️ Results are capped at 10,000 rows. Narrow your filters for full visibility."`
- Grid chart should have a border

### Drill-down (parent → detail)

If a grid is a drill-down target:
- Set `exportFieldName` and `exportParameterName` on the parent panel
- Child panel query MUST use both the exported parameter AND `{GlobalTimeRestriction}` — drill-down data must respect the same time range as the parent
- Use the exported parameter in the child query: `| where ip == '{Selected_ip}'`
- Add a markdown tooltip immediately under the parent: `"Click a row to see details below"`
- Provide a "Clear Selection" button parameter that resets the exported param
- Drilldown should have a border
- keep search on for drilldown panels

## Step 7 — Naming consistency

Across the entire workbook:
- Use **"Time"** not "Date" / "Timestamp"
- Use **"# "** or **"Count of "** or **"Number of"** consistently — pick one and stick to it throughout
- Column labels: lowercase snake_case OR Title Case — never mix within the same workbook
- Legend labels MUST match the column they group by — uniform format across all charts, no skipped items
- Axis labels use the same casing/format throughout

## Step 8 — Panel features (every panel)

Every panel content block needs:
- `"showRefreshButton": true`
- `"openLastRunQuery": true` — enables the "Open last run query" feature
- `"timeContextFromParameter": "GlobalTimeRestriction"` — global time filter must apply to every panel
- `"queryType": 0` and `"resourceType": "microsoft.operationalinsights/workspaces"`
- `"noDataMessage": "No data found."` (or a context-specific message)
- `"styleSettings": { "showBorder": true }` for visual separation in groups

## Step 9 — Chart limits enforcement

If the parser data could produce more than 100 series or 10,000 points, the panel query MUST aggregate or limit BEFORE rendering. Use `top-nested`, `bin()`, or `summarize` with explicit limits. Add a warning markdown above the panel when this cap is applied.

## Step 10 — JSON validity check

Before declaring done, run:

```python
import json
with open('<workbook_path>') as f:
    wb = json.load(f)
print('Valid. Items:', len(wb['items']))
```

If this fails, fix the JSON. Common mistakes: trailing commas, unescaped quotes inside KQL strings, missing closing braces inside long `chartSettings`.

## Step 11 — Template-safety

Before handing off:
- [ ] No static URLs in queries (e.g. no `https://my-tenant.somecorp.com`)
- [ ] No hardcoded credentials, tenant IDs, subscription IDs
- [ ] No specific resource IDs (`/subscriptions/.../workspaces/...`)
- [ ] All parameter `value` fields reset to defaults
- [ ] Every panel references the parser, NOT raw `*_CL` tables directly
- [ ] No copy-pasted complex query — common logic lives in the parser

## Step 12 — Metadata

If the user wants a deployable template (not just the raw workbook JSON), also produce:
- `fromTemplateId`: globally unique (`sentinel-<Product>_<Topic>_Dashboard`)
- A `mainTemplate` ARM wrapper with:
  - `dataTypesDependencies` — list of every `*_CL` table queried via parsers
  - `dataConnectorsDependencies` — connector IDs

Skip ARM template generation and mainTemplate generation unless asked — most workflows just need the workbook JSON.

## Step 13 — Deliverables

Output:
1. The workbook JSON file path
2. A panel inventory: title, viz type, width %, source parser, drill-down target (if any)
3. For **Migrate** mode: a field-mapping table showing source field → parser field for each panel
4. JSON validity check result
5. Any panels that depend on parsers/tables the user hasn't deployed yet (flag clearly)
6. A test plan: list the parameters and expected behaviour when each is changed

---

## Reference dashboard

- multi-tab pattern with `Tab` parameter and `conditionalVisibility` per group - `reference/CorelightDataExplorer.yaml`
