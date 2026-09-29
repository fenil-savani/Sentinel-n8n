---
name: dynatrace
description: "Dynatrace DQL extraction and semantic analysis skills for the Source Analyzer Node. TRIGGER when: source_platform is 'dynatrace' and the pipeline needs to extract fields, commands, functions, or generate summary/intent/use_case from a DQL query or Dynatrace dashboard tile JSON. DO NOT TRIGGER when: source_platform is any other value (e.g., splunk, datadog, elastic)."
---

# Source Analyzer — Dynatrace Skills

Platform-specific skills for extracting fields, commands, and functions from **Dynatrace DQL (Dynatrace Query Language)** queries and dashboard tile JSON. These skills are invoked by the Source Analyzer Node when `source_platform` is `dynatrace`.

> **Platform:** Dynatrace
> **Query Language:** DQL (Dynatrace Query Language)
> **Skills count:** 8
> **Depends on:** Context engine (`mcp__context-engine__context_engine_agent`) for all description lookups

---

## Skill 1 — Datasource Resolution (Dynatrace)

> **Name:** `datasource-resolution`
> **Description:** Determines the datasource type for the query by querying the context engine knowledge. If the context engine returns no result, infers the datasource from the DQL query structure using platform-specific heuristics.
> **When to use:** Always — this is the first skill invoked. Resolves the `datasource` field before any extraction or semantic generation begins.

**Resolution strategy:**

1. **Single context engine call** — Call `mcp__context-engine__context_engine_agent` **once** with all available inputs: `query`, `parsed_json`, `source_platform` (`dynatrace`), `metadata`, and `usage_type`. Request the full knowledge set needed for all downstream skills in one response:
   - Datasource classification
   - Identified fields in the query
   - All commands used, with descriptions and pipeline roles
   - All functions used, with descriptions and parameter signatures
   - Platform-specific documentation context for summary, intent, and use case generation

   Store the full response as `context_engine_result`. **This is the only context engine call made for the entire pipeline.**

   - If the response includes a datasource classification (e.g., `log`, `metric`, `trace`, `event`, `entity`), use it as the resolved `datasource` value.

2. **Query-based inference** — If `context_engine_result` is absent or the datasource is inconclusive, inspect the query content directly using the following Dynatrace-specific heuristics. In DQL, the `fetch` command explicitly declares the record type, making inference highly reliable:

### Dynatrace DQL datasource inference heuristics

| `fetch` record type | Inferred datasource |
|---|---|
| `fetch logs` | `log` |
| `fetch metrics` | `metric` |
| `fetch spans` | `trace` |
| `fetch events` | `event` |
| `fetch bizevents` | `event` |
| `fetch dt.entity.*` (e.g., `fetch dt.entity.host`, `fetch dt.entity.service`) | `entity` |

If `parsed_json` is present, extract the record type from the `fetch` command in the parsed structure. Otherwise, parse the first line of the raw DQL string to identify the `fetch` record type.

3. **Input value fallback** — If neither the context engine nor query inference yields a result (e.g., the query does not begin with `fetch` or the record type is unrecognized), use the `datasource` value provided in the input. If the input value is also absent, set `datasource` to `"unknown"`.

**Output:** Write the resolved value into the `datasource` field of the output.

---

## Skill 2 — Extraction Source Selection (Dynatrace)

> **Name:** `extraction-source-selection`
> **Description:** Determines the extraction source — `parsed_json` or raw DQL `query` — based on availability. Always prefer `parsed_json` when present.
> **When to use:** Always — invoked after Skill 1 and before any extraction begins.

**Dynatrace-specific guidance:**
- If `parsed_json` is present, it is a structured representation of the DQL pipeline with stages, expressions, and field references explicitly broken out. Always prefer it.
- If `parsed_json` is absent, extract from the raw DQL string directly. DQL uses a pipe-delimited (`|`) pipeline syntax similar to other query languages but with its own command and function vocabulary.

**Dynatrace query structure to expect in raw DQL:**

```
fetch <record_type>
| filter <condition>
| summarize <aggregation> [, by:{<fields>}]
| fields <field_list>
| sort <field> [asc|desc]
| limit <N>
```

- Every DQL query starts with a `fetch` command specifying the record type (`logs`, `events`, `metrics`, `spans`, `bizevents`, `dt.entity.*`).
- Each `|` separates a pipeline stage containing one command.
- Expressions inside commands can contain functions, field references, and literals.

---

## Skill 3 — Field Extraction (Dynatrace)

> **Name:** `field-extraction`
> **Description:** Identifies every data field referenced in the DQL query — including filter fields, aggregation group-by fields, computed fields, lookup/join fields, and parse-extracted fields. Uses the context engine as the primary lookup before applying static extraction rules. Returns fields categorized into `reserved_fields` (Dynatrace platform defaults) and `custom_fields` (all others).
> **When to use:** After Skill 2 has determined the extraction source. Populates the `fields` object in the output.

**Purpose:** Identify every data field referenced in the DQL query and categorize them into reserve (platform default) and custom fields.

### Output structure

The `fields` key in the output is an object with two arrays:

```json
{
  "fields": {
    "reserved_fields": ["<Dynatrace platform default fields>"],
    "custom_fields": ["<all other extracted fields>"]
  }
}
```

- **`reserved_fields`**: Dynatrace platform-specific default fields identified via context engine result. These are the entity, system, and common fields that Dynatrace provides by default (see "Dynatrace entity and log fields" section below). Use the field list from `context_engine_result` to identify which extracted fields are platform defaults — any field recognized as a Dynatrace internal/reserved field goes here.
- **`custom_fields`**: All other fields extracted from the query that are not platform defaults — user-defined, computed, parse-extracted, or enrichment fields.

### Context engine result

Use the field list from `context_engine_result` (stored in Skill 1) as the primary field list. The context engine result is also used to classify fields as reserve vs custom — fields the context engine identifies as platform defaults go into `reserved_fields`, all others into `custom_fields`. Supplement with the static extraction rules below for any fields not covered by the stored result.

### Where fields appear in DQL

| Location | Example | How to identify |
|---|---|---|
| `fetch` record type | `fetch logs` | The record type (`logs`, `metrics`, `events`, etc.) is not a field — but fields from that record type appear later |
| `filter` command | `filter status == "ERROR" AND dt.entity.host == "HOST-123"` | Field names used in comparison and boolean expressions |
| `summarize` aggregation | `summarize count(), avg(duration), by:{dt.entity.service, status}` | Fields in `by:{}` clause; fields inside aggregation function arguments (e.g., `avg(duration)` → extract `duration`). Do **not** extract bare aggregation names (`count`, `avg`, etc.) as fields — they are functions, not data fields |
| `fields` command | `fields timestamp, content, status, dt.entity.host` | All listed field names |
| `fieldsAdd` command | `fieldsAdd upper(content) AS upper_content` | Extract source fields in expressions (`content`) — do NOT extract `AS` alias (`upper_content`) |
| `fieldsRemove` command | `fieldsRemove loglevel, log.source` | Listed field names being removed |
| `fieldsRename` command | `fieldsRename status, AS, http_status` | Extract original field name (`status`) only — do NOT extract the `AS` alias (`http_status`) |
| `sort` command | `sort timestamp desc` | The field being sorted on |
| `lookup` command | `lookup [fetch dt.entity.host], sourceField:dt.entity.host, lookupField:entity.id` | Source field and lookup field |
| `makeTimeseries` command | `makeTimeseries count(), by:{status}, interval:5m` | Fields in `by:{}` clause; the time bucketing field (`timestamp` implicitly) |
| `parse` command | `parse content, "LD 'user=' LD:user_name ' '"` | Extract source field (`content`) only — do NOT extract parse-extracted aliases (`user_name`) |
| `append` command | `append [fetch events \| filter ...]` | Fields from the appended subquery — extract recursively |

### Dynatrace entity and log fields (reserve fields)

These are Dynatrace platform-specific common fields. They are **reserved fields** — but only include them in the `reserved_fields` array if they are **actually referenced in the query or parsed_json**. Do not add them just because they exist on the platform:

| Field | Description |
|---|---|
| `timestamp` | Event or log timestamp |
| `content` | Raw log content |
| `status` | Log status (`ERROR`, `WARN`, `INFO`, etc.) |
| `loglevel` | Log level |
| `log.source` | Source of the log |
| `dt.entity.host` | Dynatrace host entity ID |
| `dt.entity.service` | Dynatrace service entity ID |
| `dt.entity.process_group` | Dynatrace process group entity ID |
| `dt.entity.process_group_instance` | Dynatrace process group instance entity ID |
| `dt.source_entity` | Source entity of the record |
| `dt.system.bucket` | The Grail bucket the data is stored in |
| `event.type` | Event type identifier |
| `event.kind` | Event kind (e.g., `LOG`, `METRIC`, `BIZ_EVENT`) |
| `span.id` | Distributed trace span ID |
| `trace.id` | Distributed trace ID |
| `duration` | Duration of a span or event (in nanoseconds) |
| `http.request.method` | HTTP method (GET, POST, etc.) |
| `http.response.status_code` | HTTP response status code |
| `k8s.namespace.name` | Kubernetes namespace |
| `k8s.pod.name` | Kubernetes pod name |
| `k8s.cluster.name` | Kubernetes cluster name |

Any field identified by `context_engine_result` as a Dynatrace platform default/internal field should also be placed in `reserved_fields` — but only if that field is actually referenced in the query or parsed_json. Do not include platform defaults that are not present in the input.

### Extraction rules

- Extract field names only — not values. `status == "ERROR"` → extract `status`, not `"ERROR"`.
- Include fields in `by:{}` grouping clauses.
- For `fieldsAdd`, extract source fields in expressions only — not the `AS` alias.
- For `fieldsRename`, extract original field name only — not the `AS` target.
- For `parse`, extract the source field only — not the extracted alias names.
- Do not include record types from `fetch` (`logs`, `metrics`, etc.) as fields.
- Do not include command names, modifiers (`interval:5m`), or numeric arguments as fields.
- Do not include aggregation function names as fields — whether bare or with parentheses. The complete list: `count`, `avg`, `sum`, `min`, `max`, `median`, `stddev`, `variance`, `countIf`, `collectDistinct`, `collectArray`, `takeFirst`, `takeLast`, `takeMin`, `takeMax`, `takeAny`. Always extract fields **inside** function arguments (e.g., `avg(duration)` → extract `duration`).
- Do not include `AS` alias names as fields. Aliases are output column labels, not source data fields. Example: `summarize count() AS total_count` → do NOT extract `total_count`; `fieldsAdd duration / 1000000 AS duration_ms` → extract `duration`, NOT `duration_ms`.
- Deduplicate — each field name appears once even if referenced multiple times.
- Categorize each extracted field: if it matches a Dynatrace entity/log field (listed above) or is identified as a platform default by `context_engine_result`, place it in `reserved_fields`. All other fields go into `custom_fields`. Only fields actually referenced in the query or parsed_json are included — never add platform defaults that don't appear in the input.

**Output:** Write the `fields` object with `reserved_fields` and `custom_fields` arrays into the output.

---

## Skill 4 — Command Extraction (Dynatrace)

> **Name:** `command-extraction`
> **Description:** Identifies every DQL command in the query pipeline, fetches its description from the context engine, and writes it to the `commands` array.
> **When to use:** After Skill 2. Populates the `commands` array in the output. Uses `context_engine_result` from Skill 1 for descriptions.

**Purpose:** Identify every DQL command used in the pipeline.

### Dynatrace DQL command reference

Commands appear at the pipeline level, separated by `|`. Every DQL query begins with `fetch`.

#### Data Retrieval Commands

| Command | Description | Example |
|---|---|---|
| `fetch` | Retrieves records of a specified type from Grail. This is always the first command in a DQL query. Record types include `logs`, `events`, `metrics`, `spans`, `bizevents`, and entity types (`dt.entity.host`, `dt.entity.service`, etc.). | `fetch logs` |

#### Filtering Commands

| Command | Description | Example |
|---|---|---|
| `filter` | Filters records based on boolean expressions. Supports comparison operators (`==`, `!=`, `>`, `<`, `>=`, `<=`), logical operators (`AND`, `OR`, `NOT`), and functions. | `filter status == "ERROR" AND dt.entity.host == "HOST-ABC"` |
| `filterOut` | Removes records matching the given condition. Inverse of `filter`. | `filterOut loglevel == "NONE"` |

#### Aggregation Commands

| Command | Description | Example |
|---|---|---|
| `summarize` | Aggregates records using one or more aggregation functions. Supports `by:{}` for grouping. Collapses all records into aggregated groups. | `summarize count(), avg(duration), by:{dt.entity.service, status}` |
| `makeTimeseries` | Creates a timeseries by aggregating records into time buckets. Requires `interval:` parameter. | `makeTimeseries count(), by:{status}, interval:5m` |

#### Field Manipulation Commands

| Command | Description | Example |
|---|---|---|
| `fields` | Selects specific fields to keep in the output. All other fields are dropped. | `fields timestamp, content, status, dt.entity.host` |
| `fieldsAdd` | Creates new fields or modifies existing ones using expressions. Existing fields are preserved. | `fieldsAdd duration / 1000000 AS duration_ms` |
| `fieldsRemove` | Removes specified fields from the output. | `fieldsRemove loglevel, log.source` |
| `fieldsRename` | Renames one or more fields. | `fieldsRename status, AS, http_status` |
| `parse` | Extracts new fields from a string field using pattern matching. Supports Dynatrace Pattern Language (DPL). | `parse content, "LD 'user=' LD:user_name ' '"` |

#### Ordering & Limiting Commands

| Command | Description | Example |
|---|---|---|
| `sort` | Sorts records by one or more fields. Supports `asc` and `desc` modifiers. | `sort timestamp desc` |
| `limit` | Restricts the number of output records. | `limit 100` |

#### Combining Commands

| Command | Description | Example |
|---|---|---|
| `lookup` | Enriches records by joining with another dataset based on matching fields. | `lookup [fetch dt.entity.host], sourceField:dt.entity.host, lookupField:entity.id, fields:{entity.name}` |
| `append` | Appends results from a subquery to the current result set. | `append [fetch events \| filter event.type == "CUSTOM_ALERT"]` |
| `join` | Joins the current result set with another query on matching fields. | `join [fetch spans], on:{trace.id}, fields:{span.name, duration}` |

#### Expansion & Utility Commands

| Command | Description | Example |
|---|---|---|
| `expand` | Expands an array field into individual records (one record per array element). | `expand dt.entity.host` |

### How to identify commands in DQL

1. Split the DQL query on `|` characters (respecting subquery brackets `[...]`).
2. The first token after each `|` is the command name.
3. The first line is always a `fetch` command.
4. Subqueries (inside `[...]`) contain their own pipeline — extract commands recursively.
5. `by:{}`, `AS`, `interval:`, `sourceField:`, `lookupField:`, `fields:{}`, `on:{}` are clause parameters within commands — not separate commands.
6. `asc`, `desc` are sort modifiers — not commands.

### Context engine result

Use command descriptions from `context_engine_result` (stored in Skill 1). Map each identified command to its description from the stored result.
- If a command has no entry in `context_engine_result`: store `"description": "no description available"`.

**Output:** Write each command as `{ "name": "<command>", "description": "<description>" }` into the `commands` array.

---

## Skill 5 — Function Extraction (Dynatrace)

> **Name:** `function-extraction`
> **Description:** Identifies every function used inside DQL command arguments or expressions (e.g., `count()`, `avg()`, `matchesPhrase()`, `toLong()`), fetches its description from the context engine, and writes it to the `functions` array.
> **When to use:** After Skill 2. Populates the `functions` array in the output. Uses `context_engine_result` from Skill 1 for descriptions.

**Purpose:** Identify every function used inside DQL command arguments or expressions.

### How functions differ from commands in DQL

- **Commands** appear after `|` as the first token: `fetch`, `filter`, `summarize`.
- **Functions** appear inside command arguments as callable expressions with `()`: `count()`, `avg(duration)`, `toTimestamp(timestamp)`.
- Functions are always inside expressions — never at the pipeline level.

### Dynatrace DQL function categories

#### Aggregation Functions
Used inside `summarize` and `makeTimeseries`.

| Function | Signature | Description |
|---|---|---|
| `count()` | `count()` | Counts the number of records. |
| `countIf()` | `countIf(<condition>)` | Counts records matching a condition. |
| `avg()` | `avg(<field>)` | Returns the arithmetic mean. |
| `sum()` | `sum(<field>)` | Returns the sum. |
| `max()` | `max(<field>)` | Returns the maximum value. |
| `min()` | `min(<field>)` | Returns the minimum value. |
| `median()` | `median(<field>)` | Returns the median value. |
| `stddev()` | `stddev(<field>)` | Returns the standard deviation. |
| `variance()` | `variance(<field>)` | Returns the variance. |
| `percentile()` | `percentile(<field>, <pct>)` | Returns the specified percentile (e.g., `percentile(duration, 95)`). |
| `collectDistinct()` | `collectDistinct(<field>)` | Collects all distinct values into an array. |
| `collectArray()` | `collectArray(<field>)` | Collects all values into an array. |
| `takeFirst()` | `takeFirst(<field>)` | Returns the first value seen. |
| `takeLast()` | `takeLast(<field>)` | Returns the last value seen. |
| `takeMin()` | `takeMin(<field>)` | Returns the value from the record with the minimum. |
| `takeMax()` | `takeMax(<field>)` | Returns the value from the record with the maximum. |
| `takeAny()` | `takeAny(<field>)` | Returns any value (non-deterministic). |

#### String Functions
Used inside `filter`, `fieldsAdd`, and expressions.

| Function | Signature | Description |
|---|---|---|
| `concat()` | `concat(<str1>, <str2>, ...)` | Concatenates strings. |
| `contains()` | `contains(<string>, <substring>)` | Returns true if the string contains the substring. |
| `startsWith()` | `startsWith(<string>, <prefix>)` | Returns true if the string starts with the prefix. |
| `endsWith()` | `endsWith(<string>, <suffix>)` | Returns true if the string ends with the suffix. |
| `indexOf()` | `indexOf(<string>, <substring>)` | Returns the index of the first occurrence. |
| `matchesPhrase()` | `matchesPhrase(<field>, <phrase>)` | Checks if the field contains the phrase (optimized for log content search). |
| `matchesValue()` | `matchesValue(<field>, <pattern>)` | Matches a field value against a wildcard pattern (`*`). |
| `lower()` | `lower(<string>)` | Converts to lowercase. |
| `upper()` | `upper(<string>)` | Converts to uppercase. |
| `trim()` | `trim(<string>)` | Trims whitespace. |
| `substring()` | `substring(<string>, <start>, <length>)` | Returns a substring. |
| `replaceString()` | `replaceString(<string>, <search>, <replace>)` | Replaces all occurrences of a substring. |
| `replacePattern()` | `replacePattern(<string>, <regex>, <replace>)` | Replaces matches of a regex. |
| `splitString()` | `splitString(<string>, <delimiter>)` | Splits a string into an array. |
| `size()` | `size(<string_or_array>)` | Returns the length of a string or size of an array. |

#### Date/Time Functions

| Function | Signature | Description |
|---|---|---|
| `now()` | `now()` | Returns the current timestamp. |
| `toTimestamp()` | `toTimestamp(<value>)` | Converts a value to a timestamp. |
| `formatTimestamp()` | `formatTimestamp(<timestamp>, <format>)` | Formats a timestamp into a string using a pattern. |
| `timeframe()` | `timeframe(from: <start>, to: <end>)` | Creates a timeframe for filtering. |
| `ago()` | `ago(<duration>)` | Returns a timestamp in the past (e.g., `ago(1h)` = 1 hour ago). |
| `toUnixTimestamp()` | `toUnixTimestamp(<timestamp>)` | Converts a timestamp to Unix epoch (milliseconds). |
| `parseTimestamp()` | `parseTimestamp(<string>, <format>)` | Parses a string into a timestamp. |

#### Type Conversion Functions

| Function | Signature | Description |
|---|---|---|
| `toLong()` | `toLong(<value>)` | Converts a value to a long integer. |
| `toDouble()` | `toDouble(<value>)` | Converts a value to a double. |
| `toString()` | `toString(<value>)` | Converts a value to a string. |
| `toBoolean()` | `toBoolean(<value>)` | Converts a value to a boolean. |

#### Conditional / Logical Functions

| Function | Signature | Description |
|---|---|---|
| `if()` | `if(<condition>, <true_val>, <false_val>)` | Conditional expression returning one of two values. |
| `else` | Used with `if()` | Provides the false branch in `if()` expressions. |
| `coalesce()` | `coalesce(<val1>, <val2>, ...)` | Returns the first non-null value. |
| `isNull()` | `isNull(<value>)` | Returns true if the value is null. |
| `isNotNull()` | `isNotNull(<value>)` | Returns true if the value is not null. |
| `in()` | `in(<value>, <list>)` | Returns true if the value is in the list. |

#### Mathematical Functions

| Function | Signature | Description |
|---|---|---|
| `abs()` | `abs(<number>)` | Returns the absolute value. |
| `ceil()` | `ceil(<number>)` | Rounds up to the nearest integer. |
| `floor()` | `floor(<number>)` | Rounds down to the nearest integer. |
| `round()` | `round(<number>, <decimals>)` | Rounds to the specified number of decimal places. |
| `log()` | `log(<number>)` | Returns the natural logarithm. |
| `log10()` | `log10(<number>)` | Returns the base-10 logarithm. |
| `pow()` | `pow(<base>, <exponent>)` | Returns base raised to the exponent. |
| `sqrt()` | `sqrt(<number>)` | Returns the square root. |

#### Array Functions

| Function | Signature | Description |
|---|---|---|
| `arraySize()` | `arraySize(<array>)` | Returns the number of elements. |
| `arrayFirst()` | `arrayFirst(<array>)` | Returns the first element. |
| `arrayLast()` | `arrayLast(<array>)` | Returns the last element. |
| `arrayContains()` | `arrayContains(<array>, <value>)` | Returns true if the array contains the value. |
| `arrayDistinct()` | `arrayDistinct(<array>)` | Returns distinct elements. |

### How to identify functions in DQL

1. Scan all command arguments for tokens followed by `(...)`.
2. In `summarize` / `makeTimeseries` — aggregation expressions (e.g., `count()`, `avg(duration)`) are functions.
3. In `filter` — expressions may use functions: `filter matchesPhrase(content, "error")`.
4. In `fieldsAdd` — right-hand side expressions contain functions: `fieldsAdd toLong(duration) / 1000000 AS duration_ms`.
5. Subqueries may contain their own functions — extract recursively.
6. Do not count `by:{}`, `AS`, `interval:`, `asc`, `desc` — these are keywords/modifiers, not functions.

### Context engine result

Use function descriptions from `context_engine_result` (stored in Skill 1). Map each identified function to its description from the stored result.
- If a function has no entry in `context_engine_result`: store `"description": "no description available"`.

**Output:** Write each function as `{ "name": "<function>", "description": "<description>" }` into the `functions` array.

---

## Skill 6 — Summary Generation (Dynatrace)

> **Name:** `summary-generation`
> **Description:** Produces a detailed, self-contained, plain-language summary of the DQL query covering record types, filters, aggregations, timeseries, field transformations, and output shape.
> **When to use:** After Skills 3–5 have completed extraction. Uses extracted `fields`, `commands`, `functions`, plus `query`, `parsed_json`, `metadata`, `usage_type`, and `datasource` as context. Writes to the `summary` field.

**Purpose:** Produce a detailed summary of the Dynatrace DQL query in plain language.

### Input context

Use all of the following as context when generating the summary:
- `query` — the raw DQL string
- `parsed_json` — the parsed representation (if present)
- `fields` — extracted field names
- `commands` — extracted commands with descriptions
- `functions` — extracted functions with descriptions
- `metadata` — platform-specific metadata
- `usage_type` — how the query is used (e.g., `dashboard`, `alert`)
- `datasource` — the data type (e.g., `log`, `metric`, `trace`)

### Context engine result

Use the platform-specific documentation context from `context_engine_result` (stored in Skill 1) to enrich the summary with accurate descriptions of what each command and field does in Dynatrace.

### Dynatrace-specific context to incorporate

1. **Record type** — Describe what the `fetch` command is retrieving (e.g., "Retrieves log records from the Grail data lakehouse" for `fetch logs`; "Retrieves host entity records" for `fetch dt.entity.host`).
2. **Timeframe** — If a timeframe filter is present (e.g., `filter timestamp >= ago(24h)`), describe it (e.g., "Scoped to the last 24 hours").
3. **Entity context** — If entity fields like `dt.entity.host`, `dt.entity.service` are used, explain the Dynatrace entity model context (e.g., "Filters to a specific monitored host entity").
4. **Filters** — Describe each filter condition in plain language, including the field, operator, and value.
5. **Aggregations** — Describe the `summarize` or `makeTimeseries` logic (e.g., "Aggregates by service entity, computing the count of log records and the average duration for each group").
6. **Timeseries** — If `makeTimeseries` is used, describe the time bucketing interval and what metric is being tracked over time.
7. **Field transformations** — Describe `fieldsAdd`, `fieldsRename`, and `parse` operations (e.g., "Extracts the `user_name` from the log content using pattern matching").
8. **Lookups and joins** — Describe any enrichment from `lookup`, `join`, or `append` (e.g., "Enriches log records with host entity names by looking up `dt.entity.host`").
9. **Output shape** — Describe what the query produces (e.g., "Outputs a timeseries of error counts per service over 5-minute intervals" or "Outputs a table of the top 100 error log entries with timestamp, content, and host").
10. **Usage context** — Incorporate `usage_type` (e.g., "This query powers a Dynatrace dashboard tile") and `datasource` (e.g., "Operating on log data") where relevant.

### Quality requirements

- Must be **self-contained** — a reader unfamiliar with DQL syntax must fully understand what the query does from the summary alone.
- Must **not** reproduce raw DQL syntax without explanation.
- Must **not** be a single sentence — provide sufficient detail to reflect the full query logic.
- Should be **structured** — describe the pipeline flow sequentially, covering each stage.

**Output:** Write the generated text into the `summary` field.

---

## Skill 7 — Intent Generation (Dynatrace)

> **Name:** `intent-generation`
> **Description:** Derives a concise, goal-oriented statement of the analytical or operational objective the DQL query achieves.
> **When to use:** After Skill 6 has generated the summary. Uses the summary, query structure, `usage_type`, and `datasource` as context. Writes to the `intent` field.

**Purpose:** Derive the analytical or operational goal from the Dynatrace DQL query.

### Input context

- The `summary` produced by Skill 6
- The query structure (commands, fields, functions)
- `usage_type` and `datasource` values

### Context engine result

Use the intent pattern context from `context_engine_result` (stored in Skill 1) to match the query against known Dynatrace intent patterns and validate the derived intent before writing it.

### Dynatrace-specific intent patterns

| DQL pattern | Likely intent |
|---|---|
| `fetch logs \| filter status == "ERROR"` + `summarize count() by:{dt.entity.service}` | Identify which services are producing the most errors |
| `fetch logs \| filter matchesPhrase(content, "<keyword>")` | Search for specific log content for troubleshooting |
| `makeTimeseries count(), interval:5m` on logs | Monitor log volume trends over time for anomaly detection |
| `fetch metrics` + `summarize avg()` by entity | Track average metric values per monitored entity |
| `fetch spans \| filter duration > <threshold>` | Identify slow traces or spans for performance analysis |
| `fetch bizevents` + aggregation by business fields | Monitor business event throughput and conversion |
| `lookup [fetch dt.entity.*]` enrichment | Correlate records with entity metadata for context |
| `fetch events \| filter event.type == "CUSTOM_ALERT"` | Monitor custom alert events for incident awareness |
| `fetch dt.entity.host \| filter ...` | Inventory or health-check of monitored hosts |

### Quality requirements

- **One to three sentences** maximum.
- Must be **actionable and goal-oriented** — not a restatement of the summary.
- If the intent is ambiguous, state the most likely interpretation and **note the ambiguity** explicitly.

**Output:** Write the generated text into the `intent` field.

---

## Skill 8 — Use Case Generation (Dynatrace)

> **Name:** `use-case-generation`
> **Description:** Identifies the specific operational or business scenario the DQL query serves — e.g., error monitoring, distributed tracing, Kubernetes observability.
> **When to use:** After Skill 7 has generated the intent. Uses the intent, extracted fields/commands/functions, `usage_type`, and `datasource` as context. Writes to the `use_case` field.

**Purpose:** Identify the operational or business scenario the Dynatrace DQL query serves.

### Input context

- The `intent` produced by Skill 7
- Extracted `fields`, `commands`, `functions` and their descriptions
- `usage_type` and `datasource` values

### Context engine result

Use the use case classification context from `context_engine_result` (stored in Skill 1) to classify the query against known Dynatrace use case patterns and validate the identified scenario before writing it.

### Dynatrace-specific use case indicators

| Indicator | Use case domain |
|---|---|
| `fetch logs` with `status == "ERROR"` or `loglevel == "ERROR"` and entity fields | **Error monitoring** — detecting and analyzing application or infrastructure errors across monitored entities |
| `fetch logs` with `matchesPhrase()` or `content` filtering | **Log investigation** — searching through logs for specific patterns during incident response or troubleshooting |
| `fetch spans` with `duration`, `trace.id`, `span.id` | **Distributed tracing analysis** — identifying slow spans, bottleneck services, and trace-level latency issues |
| `fetch metrics` with `cpu`, `memory`, `disk` and entity fields | **Infrastructure monitoring** — tracking host and process health metrics |
| `fetch metrics` with `http.*` fields and `dt.entity.service` | **Service performance monitoring** — tracking request rates, error rates, and response times per service |
| `fetch bizevents` with business-domain fields | **Business analytics** — monitoring business event flows, conversions, and revenue-related metrics |
| `fetch events` with `event.type`, `event.kind` | **Event monitoring** — tracking Dynatrace problem events, custom alerts, and lifecycle events |
| `fetch dt.entity.*` with entity metadata | **Entity inventory and health** — auditing monitored topology, checking entity properties |
| `usage_type: alert` + threshold logic in `filter` | **Alerting** — real-time detection of anomalous conditions for automated incident creation |
| `usage_type: dashboard` + `makeTimeseries` | **Operational dashboards** — continuous visual monitoring of key metrics and log volumes |
| Kubernetes fields (`k8s.namespace.name`, `k8s.pod.name`) | **Kubernetes monitoring** — observability into containerized workloads and cluster health |

### Quality requirements

- Must be **specific** — identify the operational domain and describe the scenario, not just a generic label.
- Must **reference** `usage_type` and `datasource` in context where meaningful.
- Must describe **how the query result serves** the identified use case (e.g., "used to populate a Dynatrace dashboard tile tracking error rates per service").

**Output:** Write the generated text into the `use_case` field.
