---
name: Google SecOps Parser Analyser
description: Analyze Google SecOps parsers written in Logstash format and extract its fields and extractions for each field.
---
# Google SecOps Field Extraction Skill

## Description

This skill retrieves Google SecOps (Chronicle) parser configurations via the **Google SecOps MCP server**, analyzes them using Logstash-style syntax, and extracts fields and their extraction logic.

The goal is to understand how a parser processes raw logs and maps extracted values to **Chronicle UDM fields**.

---

## Inputs Received from the Field Extractor Agent

This skill is invoked by the Field Extractor Agent with the following inputs:

| Input | Description |
|---|---|
| `app_name` | The application whose logs are being parsed (e.g., `Okta`, `Windows Event Logs`) |
| `log_type` | A single log type **or a list of log types** (e.g., `OKTA`, `["WINEVTLOG", "WINDOWS_DNS"]`). Each log type maps to a specific parser on the Google SecOps MCP server. |
| `input_fields` | *(Optional)* A list of specific fields to focus on. If not provided, extract all fields. |

---

## Step 1 — Retrieve Parsers via Google SecOps MCP Server

Call the **Google SecOps MCP server** **once** with the complete list of log types to retrieve all parser configurations in a single request.

**CRITICAL: Do NOT call the MCP server multiple times with one log type at a time. Always pass all log types in a single call.**

### Preparing the MCP call

1. If `log_type` is a single value, wrap it in a list (e.g., `"OKTA"` → `["OKTA"]`).
2. If `log_type` is already a list, use it as-is.
3. Call the Google SecOps MCP server **once**, passing the full list of log types as the input parameter.

### MCP Response Format

The MCP server returns a JSON object with the following top-level structure:

```json
{
  "log_types": [ ... ],
  "summary": { ... }
}
```

- **`log_types`** — A list of objects, one per requested log type. Each object contains the retrieval result for that log type.
- **`summary`** — An overview of the request including counts and lists of successful/failed log types.

#### Per-log-type object (success)

When a log type is retrieved successfully, its object has `"status": "success"` and a nested `parser` object containing the parser code:

```json
{
  "log_type": "GCP_DNS",
  "status": "success",
  "parser": {
    "parser_id": "1070173633889959937",
    "parser_code": "<full parser .conf content>",
    "metadata": {
      "line_count": 4934,
      "type": "PREBUILT"
    }
  }
}
```

| Field | Description |
|---|---|
| `log_type` | The log type identifier (e.g., `GCP_DNS`, `CORELIGHT`) |
| `status` | `"success"` — parser was retrieved |
| `parser.parser_id` | Unique parser identifier |
| `parser.parser_code` | The full parser configuration code to analyze |
| `parser.metadata.line_count` | Number of lines in the parser code |
| `parser.metadata.type` | Parser type — `PREBUILT` (Google-provided) or `CUSTOM` (tenant-specific) |

#### Per-log-type object (failed)

When a log type fails retrieval, its object has `"status": "failed"` and an `error` field:

```json
{
  "log_type": "AWS_FIREWALL1",
  "status": "failed",
  "error": "Failed to list parsers: { \"error\": { \"code\": 404, \"message\": \"ListParsers failed\", \"status\": \"NOT_FOUND\" } }"
}
```

#### Summary object

```json
{
  "summary": {
    "total_log_types": 3,
    "successful_log_types": ["GCP_DNS", "CORELIGHT"],
    "failed_log_types": ["AWS_FIREWALL1"],
    "total_parsers": 2,
    "total_lines": 49561
  }
}
```

### Processing the MCP Response

1. Read the `summary` object first to identify which log types succeeded and which failed.
2. For each entry in the `log_types` list:
   - **If `status` is `"success"`**: Extract `parser.parser_code` and store it tagged with the `log_type` for analysis in subsequent steps.
   - **If `status` is `"failed"`**: Log the error and skip that log type:
     > "Failed to retrieve parser for log_type `[LOG_TYPE]` from Google SecOps MCP server: [ERROR]"
3. **Do NOT write parser code to the output directory** — do not create any `_parser.conf` files. The parser code is only used for in-memory analysis and should not be saved as a separate output file.
4. All subsequent steps (Step 2 onward) must be executed **for each successfully retrieved parser** independently using the `parser.parser_code` from the response.
5. Results from all successful parsers are **combined into a single CSV output**.
6. Failed log types should be reported in the completion summary but must not block processing of the successful ones.

---

## Step 2 — Load Reference Documentation

When analyzing parsers, use the following reference document for syntax understanding:

```
\google_secops_parser_syntax_reference.md```

This document explains the Google SecOps parser syntax including:

* GROK parsing
* KV parsing
* JSON parsing
* Regex extraction
* Mutate operations
* Rename operations
* Date parsing
* Conditional logic
* Field transformations

**Always refer to this document** when interpreting parser logic. Read it before beginning parser analysis.

---

## Step 3 — Understand Parser Structure

When multiple log types are provided, the MCP response contains **multiple parser codes** — one per log type. You must analyze **every parser code** returned in the response.

For each parser code:

1. Identify major sections such as:
   * input
   * filter
   * output
   * conditional blocks
2. Focus mainly on the **filter section**, where parsing logic exists.
3. Tag all findings with the corresponding `log_type` so results can be traced back to their originating parser.

---

## Step 4 — Identify Raw Fields

For each parser code, determine which raw log fields are used as sources for extraction.

Common raw fields include:

* message
* event.original
* payload
* json fields
* syslog fields

Repeat this for **every parser code** returned by the MCP server.

---

## Step 5 — Identify Extraction Logic

For each parser code, locate parsing operations such as:

   - grok patterns
   - regex
   - kv parsing
   - json parsing
   - mutate operations
   - rename operations
   - conditional logic (if/else)
   - field transformations
   - date parsing
   - intermediate fields

Preserve the **exact syntax**, including regex and grok patterns.

Example:

```
grok {
  match => { "message" => "%{IP:src_ip}" }
}
```

Repeat this for **every parser code** returned by the MCP server.

---

## Step 6 — Track Intermediate Fields

Chronicle parsers often create temporary fields before mapping to UDM.

Example transformation chain:

```
message → grok → src_ip → mutate rename → principal.ip
```

You must track the **complete transformation chain** across all parser codes.

---

## Step 7 — Identify UDM Mapping

Determine where fields are mapped to Chronicle UDM fields.

Examples:

```
principal.ip
target.ip
principal.user.userid
target.hostname
security_result.action
metadata.event_type
```

---

## Step 8 — Preserve Conditional Logic

If extraction occurs inside conditionals, include the condition.

Example:

```
if [event_type] == "login" {
   mutate {
      rename => { "user" => "principal.user.userid" }
   }
}
```

Do not remove or simplify conditions.

---

## Processing Logic

Process **all parser codes** returned by the MCP server. For every field (or every field in the **input_fields** list if provided):

1. Search **each parser code** for occurrences of that field.
2. Identify:

   * The **raw field source**
   * The **extraction logic**
   * Any **intermediate fields**
   * The final **UDM field mapping**

3. If the same field appears in multiple parser codes, include a separate extraction entry for each parser, tagged with its `log_type`.

Trace the complete transformation chain:

```
Raw Field → Extraction Logic → Intermediate Fields → UDM Field
```

Combine the field extractions from **all parsers** into a single unified result set.

---

## Step 9 — Write CSV Output

The final result must be written to a CSV file. Do not print results as plain text.

The Field Extractor Agent specifies the output path. This skill produces the CSV content with the following columns:

```
Input Field,LogType,Extraction Logic
```

### Column Values

| Column | Value |
|---|---|
| `Input Field` | The raw or source field name as it appears in the parser |
| `LogType` | The log type that this extraction originates from. When the same field appears in multiple parser codes, each parser produces its own row with its respective log type. |
| `Extraction Logic` | The exact extraction logic verbatim from the parser |

### Rules

* Include every field from the parser file (unless `input_fields` restricts the scope).
* Preserve the exact syntax of extraction logic.
* Maintain grok patterns, regex, and kv syntax exactly.
* Include conditional logic if present.
* If a field has multiple extraction steps, list each as a separate row.
* If no extraction is found for a field, set `Extraction Logic` to `No extraction found in this parser`.
* When multiple log types are processed, combine all results into a single CSV with the `LogType` column distinguishing rows.
* If the same field is extracted in multiple parser codes (i.e., multiple log types), include a **separate row for each log type** with its own extraction logic.

---

## Example CSV Output

```csv
Input Field,LogType,Extraction Logic
src_ip,WINEVTLOG,grok { match => { "message" => "%{IP:src_ip}" } }
src_ip,NETSKOPE,grok { match => { "message" => "%{IP:src_ip}" } }
dst_ip,NETSKOPE,grok { match => { "message" => "%{IP:dst_ip}" } } merge => {
              "event.idm.read_only_udm.target.ip" => "dst_ip"
              "event.idm.read_only_udm.target.asset.ip" => "dst_ip"
            }
username,OKTA,message, kv { source => "message" }
action,OKTA,"if [action] == ""allow"" { mutate { replace => { ""security_result.action"" => ""ALLOW"" } } }"
```

In this example, three log types (`WINEVTLOG`, `NETSKOPE`, `OKTA`) were processed. The field `src_ip` was found in both the `WINEVTLOG` and `NETSKOPE` parsers, so it appears as two separate rows — one per log type with its respective extraction logic.

---

## Output Rules

Follow these rules strictly:

1. Do not summarize extraction logic
2. Preserve regex and grok patterns exactly
3. Maintain indentation in code examples
4. Include intermediate transformation steps
5. List extraction steps in the order they occur in the parser

---

## Important Guidelines

Always trace the full mapping chain:

Raw Log Field → Extraction Logic → Intermediate Fields → UDM Field

Never modify parser syntax.

Never remove extraction steps.

Always preserve exact parsing logic.

This ensures accurate analysis of Chronicle parsers.
