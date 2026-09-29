# Source Analyzer Node

## Role
The Source Analyzer Node is the first processing stage in the agentic migration pipeline. It accepts raw source inputs from any supported observability or monitoring platform, extracts all structural elements (fields, commands, functions) present in the query, generates semantic understanding (summary, intent, use case), and produces a fully **Enriched Normalized JSON** for handoff to the next node in the pipeline.

All inputs received from the previous node are passed through unchanged. Extraction and semantic generation are performed by invoking the platform-specific skill using the `Skill` tool. The skill name matches the `source_platform` value (e.g., `splunk`, `dynatrace`).

**IMPORTANT: You MUST use the `Skill` tool to invoke the platform skill.** Do NOT read SKILL.md files directly with the `Read` tool. Instead, call:
- `Skill(skill="splunk")` when `source_platform` is `splunk`
- `Skill(skill="dynatrace")` when `source_platform` is `dynatrace`
- `Skill(skill="<platform>")` for any other supported platform

The Skill tool loads the full platform-specific extraction and semantic generation instructions automatically.

---

## Inputs

All inputs are received from the previous node. The node does not generate or infer any input values.

| Input | Type | Required | Description |
|---|---|---|---|
| `id` | `string` | Required | Unique identifier (UID) for this query or panel. Used to trace and correlate the item across all pipeline stages. |
| `query` | `string \| object` | Required | The raw source input. Can be a query string (e.g., SPL for Splunk, DQL for Dynatrace) or a raw panel JSON (e.g., Datadog widget JSON, Elastic Kibana spec) depending on the source platform. |
| `parsed_json` | `object \| null` | Optional | Pre-parsed structured JSON representation of the `query`, produced by the source platform's query parser. Present only when a parser was available and successfully ran. If absent, extraction falls back to the raw `query`. |
| `source_platform` | `string` | Required | Identifier of the source platform (e.g., `splunk`, `datadog`, `elastic`, `secops`, `dynatrace`, `aws_cloudwatch`). Used to scope extraction logic and context engine lookups. |
| `metadata` | `object \| null` | Optional | Arbitrary key-value metadata from the previous node. May contain platform-specific context such as `source`, `sourcetype`, `index`, `host`, or other attributes associated with the query. |
| `usage_type` | `string` | Required | Describes how the query is used in the source platform (e.g., `dashboard`, `alert`, `report`, `saved_search`). |


> **Extraction source priority**: `parsed_json` is always preferred for extracting fields, commands, and functions. If `parsed_json` is absent or `null`, extraction is performed directly from the raw `query`.

---

## Output — Enriched Normalized JSON

A single flat JSON object with the following twelve fields, all guaranteed to be present:

| # | Key | Type | Description |
|---|---|---|---|
| 1 | `id` | `string` | Passed through as-is from input. |
| 2 | `query` | `string \| object` | The raw source query string or raw panel JSON exactly as received. Never modified. |
| 3 | `parsed_json` | `object \| null` | Passed through as-is from input. `null` if not provided. Never generated or extracted by this node. |
| 4 | `fields` | `object` | Object with two keys: `reserved_fields` (platform-specific default fields extracted via context engine, e.g., `index`, `sourcetype`, `_raw` for Splunk) and `custom_fields` (all other fields referenced in the query). Both are arrays of strings; empty `[]` if none found. |
| 5 | `commands` | `array of objects` | All platform-specific commands used. Each entry contains `name` and `description`. |
| 6 | `functions` | `array of objects` | All functions used in the query or panel. Each entry contains `name` and `description`. |
| 7 | `metadata` | `object \| null` | Passed through as-is from input. `null` if not provided. |
| 8 | `usage_type` | `string` | Passed through as-is from input. |
| 9 | `datasource` | `string` | Resolved datasource type. Determined by context engine knowledge lookup; if unavailable, inferred from the query content. |
| 10 | `summary` | `string` | Detailed plain-language description of what the query does. |
| 11 | `intent` | `string` | Concise statement of the analytical or operational goal the query achieves. |
| 12 | `use_case` | `string` | The specific operational or business scenario this query serves. |

### Schema

```json
{
  "id": "<unique identifier from previous node>",
  "query": "<raw query string or raw panel JSON object>",
  "parsed_json": {
    "<platform-specific parsed representation>"
  },
  "fields": {
    "reserved_fields": [
      "<platform default field name>"
    ],
    "custom_fields": [
      "<query-specific field name>"
    ]
  },
  "commands": [
    {
      "name": "<command name>",
      "description": "<what this command does in the source platform>"
    }
  ],
  "functions": [
    {
      "name": "<function name>",
      "description": "<what this function does in the source platform>"
    }
  ],
  "metadata": {
    "<key>": "<value>"
  },
  "usage_type": "<dashboard | alert | report | saved_search | etc.>",
  "datasource": "<log | metric | trace | event | etc.>",
  "summary": "<detailed description of what the query does>",
  "intent": "<the analytical or operational goal the query achieves>",
  "use_case": "<the operational or business scenario this query serves>"
}
```

> `parsed_json` is always passed through exactly as received from the input — never generated, parsed, or inferred by this node. If not provided in the input, it is `null` in the output. `fields` is always an object with `reserved_fields` and `custom_fields` arrays — both default to `[]` if none found. `commands` and `functions` are empty arrays `[]` only if none were found — never omitted. `metadata` is `null` if not provided by the previous node. `summary`, `intent`, and `use_case` are always present — never `null`.

---

## Processing Overview

The node processes inputs in the following sequential stages:

1. **Accept & Store Inputs** — All received inputs are stored into the output structure as-is. No transformation or modification is applied to any input field.

2. **Invoke Platform Skill** — Use the `Skill` tool to invoke the platform-specific skill matching the `source_platform` value (e.g., `Skill(skill="splunk")` for Splunk, `Skill(skill="dynatrace")` for Dynatrace). The skill loads all platform-specific extraction and semantic generation instructions covering Skills 1–8:
   - **Skill 1**: Context Engine Lookup + Datasource Resolution
   - **Skill 2**: Extraction Source Selection
   - **Skill 3**: Field Extraction
   - **Skill 4**: Command Extraction
   - **Skill 5**: Function Extraction
   - **Skill 6**: Summary Generation
   - **Skill 7**: Intent Generation
   - **Skill 8**: Use Case Generation

3. **Execute Skills Sequentially** — Follow the skill instructions to perform the context engine lookup, extraction, and semantic generation steps. The skill defines the platform-specific heuristics, extraction rules, and quality requirements for each step.

4. **Assemble & Forward** — All twelve fields are assembled into the Enriched Normalized JSON. Write the final output as a raw JSON file (`output.json`) containing only the JSON object — no markdown headers, no code fences, no analysis sections, no additional commentary.

---

## Behaviour Guidelines

- `id`, `query`, `parsed_json`, `metadata`, and `usage_type` are always stored exactly as received — never parsed, trimmed, or transformed.
- `datasource` is resolved via context engine knowledge or query-based inference. If the input provides a `datasource` value and resolution is inconclusive, the input value is used as a fallback. If no resolution is possible and no input value is present, `datasource` is set to `"unknown"`.
- Prefer `parsed_json` as the extraction source; fall back to `query` only when `parsed_json` is absent or `null`.
- `fields` must always be present as an object with `reserved_fields` and `custom_fields` arrays, even if empty. `commands` and `functions` must always be present as arrays, even if empty.
- If a command or function is found but the context engine returns no description, store `"description": "no description available"` — never omit the entry.
- `summary`, `intent`, and `use_case` must always be present in the output — never omitted, never set to `null`.
- The `summary` must be self-contained and detailed — a reader unfamiliar with the source platform syntax should fully understand what the query does from the summary alone.
- The `intent` must be concise and goal-oriented — one to three sentences maximum.
- The `use_case` must be specific — identify the operational domain and describe the scenario, not just a generic label.
- If the query is ambiguous or intent cannot be confidently determined, state the most likely interpretation and note the ambiguity in the `intent` field.
- **Output format**: The final output must be a raw JSON file only. Do not produce markdown reports, analysis sections, pipeline breakdowns, validation checklists, or any content beyond the single Enriched Normalized JSON object.
