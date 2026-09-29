---
name: splunk
description: "Splunk SPL extraction and semantic analysis skills for the Source Analyzer Node. TRIGGER when: source_platform is 'splunk' and the pipeline needs to extract fields, commands, functions, or generate summary/intent/use_case from an SPL query or Splunk dashboard panel JSON. DO NOT TRIGGER when: source_platform is any other value (e.g., dynatrace, datadog, elastic)."
---

# Source Analyzer — Splunk Skills

Platform-specific skills for extracting fields, commands, and functions from **Splunk SPL** queries and dashboard panel JSON. These skills are invoked by the Source Analyzer Node when `source_platform` is `splunk`.

> **Platform:** Splunk
> **Query Language:** SPL (Search Processing Language)
> **Skills count:** 8
> **Depends on:** Context engine (`mcp__context-engine__context_engine_agent`) for all description lookups

---

## Skill 1 — Datasource Resolution (Splunk)

> **Name:** `datasource-resolution`
> **Description:** Determines the datasource type for the query by querying the context engine knowledge. If the context engine returns no result, infers the datasource from the Splunk query structure using platform-specific heuristics.
> **When to use:** Always — this is the first skill invoked. Resolves the `datasource` field before any extraction or semantic generation begins.

**Resolution strategy:**

1. **Single context engine call** — Call `mcp__context-engine__context_engine_agent` **once** with all available inputs: `query`, `parsed_json`, `source_platform` (`splunk`), `metadata` (e.g., `index`, `sourcetype`, `source`, `host`), and `usage_type`. Request the full knowledge set needed for all downstream skills in one response:
   - Datasource classification
   - Identified fields in the query
   - All commands used, with descriptions and pipeline roles
   - All functions used, with descriptions and parameter signatures
   - Platform-specific documentation context for summary, intent, and use case generation

   Store the full response as `context_engine_result`. **This is the only context engine call made for the entire pipeline.**

   - If the response includes a datasource classification (e.g., `log`, `metric`, `trace`, `event`), use it as the resolved `datasource` value.

2. **Query-based inference** — If `context_engine_result` is absent or the datasource is inconclusive, inspect the query content directly using the following Splunk-specific heuristics:

### Splunk datasource inference heuristics

| Signal | Inferred datasource |
|---|---|
| `index=_metrics` present or `\| mstats` command used | `metric` |
| `sourcetype=stash` or `sourcetype=stash_new` | `metric` |
| Metric-specific fields present (`metric_name:`, `_value`) | `metric` |
| `sourcetype=access_combined`, `sourcetype=syslog`, `sourcetype=linux_secure`, `sourcetype=wineventlog`, or similar text-based log sourcetypes | `log` |
| Log-related fields present (`_raw`, `linecount`) with no metric indicators | `log` |
| `index=` present with no metric-specific indicators | `log` (default for Splunk index-based searches) |
| APM or trace-related fields present (`trace_id`, `span_id`, `duration`, `service_name`) | `trace` |
| Event-driven fields present (`action`, `signature`, `event_type`, `vendor_action`) without log indicators | `event` |

3. **Input value fallback** — If neither the context engine nor query inference yields a result, use the `datasource` value provided in the input. If the input value is also absent, set `datasource` to `"unknown"`.

**Output:** Write the resolved value into the `datasource` field of the output.

---

## Skill 2 — Extraction Source Selection (Splunk)

> **Name:** `extraction-source-selection`
> **Description:** Determines the extraction source — `parsed_json` or raw SPL `query` — based on availability. Always prefer `parsed_json` when present.
> **When to use:** Always — invoked after Skill 1 and before any extraction begins.

**Splunk-specific guidance:**
- If `parsed_json` is present, it is typically the output of the Splunk SPL parser and contains a structured AST-like representation of the search pipeline with commands, arguments, and field references explicitly broken out. Always prefer it.
- If `parsed_json` is absent, extract from the raw SPL string directly. SPL uses a pipe-delimited (`|`) pipeline syntax where each stage is a command with arguments.

**Splunk query structure to expect in raw SPL:**

```
<base search clause> | <command1> <args> | <command2> <args> | ...
```

- The base search clause (before the first `|`) is an implicit `search` command.
- Each `|` separates a pipeline stage containing one command.
- Arguments to commands can include field names, expressions, functions, and literals.

---

## Skill 3 — Field Extraction (Splunk)

> **Name:** `field-extraction`
> **Description:** Identifies every data field referenced in the SPL query — including base search fields, command arguments, computed fields, lookup fields, and regex-extracted fields. Uses the context engine as the primary lookup before applying static extraction rules. Returns fields categorized into `reserved_fields` (Splunk platform defaults) and `custom_fields` (all others).
> **When to use:** After Skill 2 has determined the extraction source. Populates the `fields` object in the output.

**Purpose:** Identify every data field referenced in the SPL query and categorize them into reserve (platform default) and custom fields.

### Output structure

The `fields` key in the output is an object with two arrays:

```json
{
  "fields": {
    "reserved_fields": ["<Splunk platform default fields>"],
    "custom_fields": ["<all other extracted fields>"]
  }
}
```

- **`reserved_fields`**: Splunk platform-specific default fields identified via context engine result. These are the internal/system fields that Splunk provides by default (see "Splunk internal fields" section below). Use the field list from `context_engine_result` to identify which extracted fields are platform defaults — any field recognized as a Splunk internal/reserved field goes here.
- **`custom_fields`**: All other fields extracted from the query that are not platform defaults — user-defined, computed, lookup-enriched, or regex-extracted fields.

### Context engine result

Use the field list from `context_engine_result` (stored in Skill 1) as the primary field list. The context engine result is also used to classify fields as reserve vs custom — fields the context engine identifies as platform defaults go into `reserved_fields`, all others into `custom_fields`. Supplement with the static extraction rules below for any fields not covered by the stored result.

### Where fields appear in SPL

| Location | Example | How to identify |
|---|---|---|
| Base search clause | `index=main sourcetype=syslog src_ip=10.0.0.1` | `<field>=<value>` pairs; the field name is everything before the `=` |
| `search` command | `\| search status_code>=400` | Same as base search — field comparisons |
| `where` command | `\| where response_time > 5000` | Field names used in boolean expressions |
| `eval` command (RHS) | `\| eval duration = end_time - start_time` | Fields referenced on the right-hand side of the `=` |
| `eval` command (LHS) | `\| eval duration = ...` | LHS is a computed name — do NOT extract it as a field |
| `stats` / `eventstats` / `streamstats` | `\| stats dc(query) as unique_queries count by src_ip, domain` | Extract `by` clause fields (`src_ip`, `domain`) and function arguments (`query` from `dc(query)`). Do NOT extract aggregation names (`dc`, `count`) or `AS` aliases (`unique_queries`) |
| `timechart` / `chart` | `\| timechart span=1h avg(cpu_usage) by host` | Fields in `by` clause; fields inside functions; `span` is a modifier, not a field |
| `table` / `fields` | `\| table _time, src_ip, action, status` | All listed field names are fields |
| `rename` | `\| rename src_ip AS source_ip` | Extract original field name (`src_ip`) only — do NOT extract the `AS` alias (`source_ip`) |
| `rex` | `\| rex field=_raw "(?<user_name>\w+)"` | Extract source field (`_raw`) only — do NOT extract named capture group aliases (`user_name`) |
| `lookup` | `\| lookup geo_lookup ip AS src_ip OUTPUT city, country` | Extract lookup key fields (`ip`, `src_ip`) — do NOT extract `OUTPUT` alias fields (`city`, `country`) |
| `dedup` | `\| dedup src_ip, dest_port` | All listed field names |
| `sort` | `\| sort - _time` | The field being sorted on (`_time`); but see exclusion rules — do not extract aggregation output names (e.g., `count`) that are not actual data fields |
| `head` / `tail` | `\| head 10` | No fields — numeric argument only |
| `join` | `\| join src_ip [search index=threat_intel]` | The join key (`src_ip`) is a field |
| `fillnull` | `\| fillnull value=0 bytes_in bytes_out` | Listed field names |
| `spath` | `\| spath input=_raw path=user.name output=user_name` | Extract source field (`_raw`) — do NOT extract `output` alias (`user_name`) |

### Splunk internal fields (reserve fields)

These are Splunk platform-specific internal fields. They are **reserved fields** — but only include them in the `reserved_fields` array if they are **actually referenced in the query or parsed_json**. Do not add them just because they exist on the platform:

| Field | Description |
|---|---|
| `_time` | Event timestamp |
| `_raw` | Raw event text |
| `index` | The Splunk index being searched |
| `sourcetype` | The sourcetype of the data |
| `source` | The data source path |
| `host` | The originating host |
| `_serial` | Event serial number |
| `linecount` | Number of lines in the event |

Any field identified by `context_engine_result` as a Splunk platform default/internal field should also be placed in `reserved_fields` — but only if that field is actually referenced in the query or parsed_json. Do not include platform defaults that are not present in the input.

### Extraction rules

- Extract field names only — not values. `src_ip=10.0.0.1` → extract `src_ip`, not `10.0.0.1`.
- For `eval`, only extract RHS referenced fields — not the LHS computed name.
- For `rex`, extract the source field (`field=_raw`) — not the named capture group aliases.
- For `rename`, extract the original field name only — not the `AS` target.
- For `lookup`, extract the lookup key fields only — not the `OUTPUT` alias fields.
- Do not include command names, modifiers (`span=1h`), or numeric arguments as fields.
- Do not include string literals or wildcard patterns as fields.
- Do not include aggregation function names as fields — whether they appear with or without parentheses. In SPL, aggregation functions can appear **with parentheses** (`count()`, `dc(query)`, `avg(bytes)`) or **without parentheses** (`count`, `dc`, `avg`) as shorthand. In both cases, the function name itself is NOT a data field. The complete list of aggregation function names to exclude: `count`, `avg`, `sum`, `min`, `max`, `dc`, `distinct_count`, `median`, `mode`, `stdev`, `var`, `range`, `first`, `last`, `list`, `values`, `earliest`, `latest`, `perc`, `percentile`, `rate`, `sumsq`, `upper`, `lower` (when used as aggregation, not string function).
  - **Example:** `stats dc(query) as unique_queries count by src_ip, domain` → extract `query` (argument of `dc()`), `src_ip` and `domain` (`by` clause). Do NOT extract `count` (bare aggregation), `dc` (function name), or `unique_queries` (`AS` alias).
  - Always extract fields **inside** aggregation function arguments (e.g., `dc(query)` → extract `query`, `avg(response_time)` → extract `response_time`).
- Do not include `AS` alias names as fields. Aliases are output column labels, not source data fields. Examples: `stats count AS total` → do NOT extract `total`; `dc(query) as unique_queries` → do NOT extract `unique_queries`; `rename src_ip AS source_ip` → extract `src_ip` only, NOT `source_ip`.
- Do not include `eval` LHS (left-hand side) computed field names as fields. The LHS is a new derived label, not a source data field. Only extract the RHS referenced fields. Example: `eval duration = end_time - start_time` → extract `end_time` and `start_time`, do NOT extract `duration`.
- Deduplicate — each field name appears once even if referenced multiple times.
- Categorize each extracted field: if it matches a Splunk internal field (listed above) or is identified as a platform default by `context_engine_result`, place it in `reserved_fields`. All other fields go into `custom_fields`. Only fields actually referenced in the query or parsed_json are included — never add platform defaults that don't appear in the input.

**Output:** Write the `fields` object with `reserved_fields` and `custom_fields` arrays into the output.

---

## Skill 4 — Command Extraction (Splunk)

> **Name:** `command-extraction`
> **Description:** Identifies every SPL command in the query pipeline, fetches its description from the context engine, and writes it to the `commands` array.
> **When to use:** After Skill 2. Populates the `commands` array in the output. Requires context engine lookup for each command.

**Purpose:** Identify every SPL command used in the pipeline.

### Splunk SPL command reference

Commands appear at the pipeline level, separated by `|`. The base search clause is an implicit `search` command.

#### Search & Filtering Commands

| Command | Description | Example |
|---|---|---|
| `search` | Filters events based on field values, keywords, and boolean expressions. The base search clause before the first pipe is an implicit `search`. | `search index=main sourcetype=access_combined status=500` |
| `where` | Filters events using an eval-style boolean expression. Unlike `search`, supports functions and complex expressions. | `\| where like(uri, "%admin%") AND status >= 400` |

#### Transformation & Aggregation Commands

| Command | Description | Example |
|---|---|---|
| `stats` | Computes aggregate statistics over the dataset. Supports `by` clause for grouping. The most common aggregation command. | `\| stats count, avg(response_time) by status_code, host` |
| `eventstats` | Same as `stats` but appends aggregation results to each event without collapsing rows. | `\| eventstats avg(bytes) AS avg_bytes by sourcetype` |
| `streamstats` | Computes running/cumulative statistics across events in order. | `\| streamstats count AS event_num by session_id` |
| `timechart` | Aggregates data into time buckets for timeseries visualization. Always groups by `_time`. | `\| timechart span=1h count by status` |
| `chart` | Creates a table of aggregate values, pivoted by one or more fields. Like `stats` but with a split-by field as columns. | `\| chart avg(cpu) over host by datacenter` |
| `top` | Returns the most common values of a field with count and percentage. | `\| top limit=10 src_ip` |
| `rare` | Returns the least common values of a field. Opposite of `top`. | `\| rare limit=5 user_agent` |

#### Field Manipulation Commands

| Command | Description | Example |
|---|---|---|
| `eval` | Creates or overwrites fields using expressions and functions. Supports arithmetic, string, conditional, and multivalue functions. | `\| eval duration = round((end_time - start_time) / 1000, 2)` |
| `rex` | Extracts fields from a source field using regular expressions with named capture groups. Can also operate in `mode=sed` for replacement. | `\| rex field=_raw "user=(?<username>[^\s]+)"` |
| `rename` | Renames one or more fields. | `\| rename src_ip AS source_address, dest_ip AS destination_address` |
| `fields` | Keeps (`+`) or removes (`-`) fields from the results. | `\| fields + _time, host, src_ip, status` |
| `table` | Formats results as a table with the specified fields in order. | `\| table _time, src_ip, dest_ip, action` |
| `fillnull` | Replaces null field values with a specified value. | `\| fillnull value="N/A" city, country` |
| `spath` | Extracts fields from structured data (JSON/XML) in a field. | `\| spath input=_raw path=user.email output=email` |

#### Ordering & Deduplication Commands

| Command | Description | Example |
|---|---|---|
| `sort` | Sorts results by one or more fields. Prefix `-` for descending, `+` for ascending. | `\| sort - count, + host` |
| `dedup` | Removes duplicate events based on specified fields. | `\| dedup src_ip, dest_port` |
| `head` | Returns the first N results. | `\| head 20` |
| `tail` | Returns the last N results. | `\| tail 5` |
| `reverse` | Reverses the order of results. | `\| reverse` |

#### Lookup & Enrichment Commands

| Command | Description | Example |
|---|---|---|
| `lookup` | Enriches events by matching field values against a lookup table (CSV, KV store). | `\| lookup threat_intel_lookup ip AS src_ip OUTPUT threat_category, threat_score` |
| `inputlookup` | Loads the entire contents of a lookup table as events. | `\| inputlookup geo_ip_lookup` |
| `outputlookup` | Writes results to a lookup table. | `\| outputlookup my_results.csv` |

#### Combining Commands

| Command | Description | Example |
|---|---|---|
| `join` | Joins results with a subsearch based on a common field. | `\| join type=inner src_ip [search index=threat_intel]` |
| `append` | Appends results from a subsearch to the current result set. | `\| append [search index=summary report=daily_count]` |
| `appendpipe` | Appends the results of a pipeline applied to the current results. | `\| appendpipe [stats sum(count) AS total]` |
| `union` | Combines results from multiple datasets. | `\| union [search index=web], [search index=app]` |

#### Formatting & Output Commands

| Command | Description | Example |
|---|---|---|
| `convert` | Converts field values between formats (e.g., epoch to human time, kilobytes to megabytes). | `\| convert ctime(_time) AS readable_time` |
| `addinfo` | Adds search metadata fields (`info_min_time`, `info_max_time`, `info_search_time`). | `\| addinfo` |
| `transaction` | Groups events into transactions based on shared field values and time constraints. | `\| transaction session_id maxpause=5m` |
| `bucket` / `bin` | Groups numeric or time values into discrete buckets. | `\| bucket _time span=1h` |
| `multisearch` | Runs multiple streaming searches simultaneously. | `\| multisearch [search index=web] [search index=app]` |

### How to identify commands

1. Split the SPL query on `|` characters (respecting subsearch brackets `[...]`).
2. The first token after each `|` is the command name.
3. The base search clause (before the first `|`) is an implicit `search` command.
4. Subsearches (inside `[...]`) contain their own pipeline — extract commands from those recursively.
5. `by`, `AS`, `OUTPUT`, `WHERE`, `OVER` are clauses/keywords within commands — not separate commands.

### Context engine result

Use command descriptions from `context_engine_result` (stored in Skill 1). Map each identified command to its description from the stored result.
- If a command has no entry in `context_engine_result`: store `"description": "no description available"`.

**Output:** Write each command as `{ "name": "<command>", "description": "<description>" }` into the `commands` array.

---

## Skill 5 — Function Extraction (Splunk)

> **Name:** `function-extraction`
> **Description:** Identifies every function used inside SPL command arguments or expressions (e.g., `count()`, `avg()`, `if()`, `coalesce()`), fetches its description from the context engine, and writes it to the `functions` array.
> **When to use:** After Skill 2. Populates the `functions` array in the output. Requires context engine lookup for each function.

**Purpose:** Identify every function used inside SPL command arguments or expressions.

### How functions differ from commands in SPL

- **Commands** appear after `|` as the first token: `| stats`, `| eval`, `| rex`.
- **Functions** appear inside command arguments as callable expressions with `()`: `count()`, `avg(bytes)`, `if(status=200, "ok", "fail")`.
- Some SPL words can be both: `rex` is a command (`| rex ...`) but also a function inside `eval` (`| eval x = rex(...)` — rare). Extract based on position.

### Splunk function categories

#### Aggregation Functions
Used inside `stats`, `timechart`, `chart`, `eventstats`, `streamstats`.

| Function | Signature | Description |
|---|---|---|
| `count()` | `count(<field>)` or `count()` | Counts events. With a field argument, counts events where that field is present. |
| `dc()` / `distinct_count()` | `dc(<field>)` | Counts distinct values of a field. |
| `avg()` | `avg(<field>)` | Returns the arithmetic mean of a numeric field. |
| `sum()` | `sum(<field>)` | Returns the sum of a numeric field. |
| `max()` | `max(<field>)` | Returns the maximum value of a field. |
| `min()` | `min(<field>)` | Returns the minimum value of a field. |
| `median()` | `median(<field>)` | Returns the median value of a numeric field. |
| `mode()` | `mode(<field>)` | Returns the most frequent value of a field. |
| `stdev()` | `stdev(<field>)` | Returns the standard deviation. |
| `var()` | `var(<field>)` | Returns the variance. |
| `percentile()` / `perc()` / `p()` | `perc<N>(<field>)` e.g., `perc95(response_time)` | Returns the Nth percentile. |
| `range()` | `range(<field>)` | Returns the difference between max and min. |
| `first()` | `first(<field>)` | Returns the first seen value in time order. |
| `last()` | `last(<field>)` | Returns the last seen value in time order. |
| `list()` | `list(<field>)` | Returns all values as a multivalue field. |
| `values()` | `values(<field>)` | Returns all distinct values as a multivalue field. |
| `earliest()` | `earliest(<field>)` | Returns the value from the earliest event. |
| `latest()` | `latest(<field>)` | Returns the value from the latest event. |

#### Eval Functions — Conditional

| Function | Signature | Description |
|---|---|---|
| `if()` | `if(<condition>, <true_value>, <false_value>)` | Returns one of two values based on a boolean condition. |
| `case()` | `case(<cond1>, <val1>, <cond2>, <val2>, ...)` | Evaluates conditions in order and returns the value paired with the first true condition. |
| `coalesce()` | `coalesce(<field1>, <field2>, ...)` | Returns the first non-null value from the given fields. |
| `null()` | `null()` | Returns NULL. Used in `case()` and `if()` for default handling. |
| `validate()` | `validate(<cond1>, <msg1>, <cond2>, <msg2>, ...)` | Returns the message for the first condition that is false. |
| `match()` | `match(<field>, <regex>)` | Returns true if the field matches the regular expression. |
| `like()` | `like(<field>, <pattern>)` | Returns true if the field matches the SQL-like pattern (`%`, `_`). |
| `in()` | `in(<field>, <val1>, <val2>, ...)` | Returns true if the field value is in the given list. |
| `cidrmatch()` | `cidrmatch(<cidr>, <ip_field>)` | Returns true if the IP matches the CIDR range. |

#### Eval Functions — String

| Function | Signature | Description |
|---|---|---|
| `len()` | `len(<string>)` | Returns the character length. |
| `lower()` | `lower(<string>)` | Converts to lowercase. |
| `upper()` | `upper(<string>)` | Converts to uppercase. |
| `trim()` | `trim(<string>, <chars>)` | Trims specified characters from both ends. |
| `ltrim()` | `ltrim(<string>, <chars>)` | Trims from the left. |
| `rtrim()` | `rtrim(<string>, <chars>)` | Trims from the right. |
| `substr()` | `substr(<string>, <start>, <length>)` | Returns a substring. |
| `replace()` | `replace(<string>, <regex>, <replacement>)` | Regex-based string replacement. |
| `split()` | `split(<string>, <delimiter>)` | Splits a string into a multivalue field. |
| `urldecode()` | `urldecode(<url>)` | Decodes a URL-encoded string. |

#### Eval Functions — Type Conversion

| Function | Signature | Description |
|---|---|---|
| `tostring()` | `tostring(<value>, <format>)` | Converts a value to a string. `format` can be `"commas"`, `"hex"`, `"duration"`. |
| `tonumber()` | `tonumber(<string>, <base>)` | Converts a string to a number. Optional base (default 10). |
| `printf()` | `printf(<format_string>, <args>)` | C-style string formatting. |

#### Eval Functions — Date/Time

| Function | Signature | Description |
|---|---|---|
| `now()` | `now()` | Returns the current epoch time. |
| `time()` | `time()` | Returns the current time as epoch seconds. |
| `relative_time()` | `relative_time(<time>, <modifier>)` | Adjusts epoch time by a relative time modifier (e.g., `"-1h"`). |
| `strftime()` | `strftime(<epoch>, <format>)` | Formats epoch time into a human-readable string. |
| `strptime()` | `strptime(<string>, <format>)` | Parses a time string into epoch time. |

#### Eval Functions — Multivalue

| Function | Signature | Description |
|---|---|---|
| `mvcount()` | `mvcount(<mvfield>)` | Returns the number of values in a multivalue field. |
| `mvindex()` | `mvindex(<mvfield>, <start>, <end>)` | Returns a subset of values by index. |
| `mvfilter()` | `mvfilter(<expression>)` | Filters multivalue field by expression. |
| `mvjoin()` | `mvjoin(<mvfield>, <delimiter>)` | Joins multivalue field into a single string. |
| `mvappend()` | `mvappend(<val1>, <val2>, ...)` | Combines values into a multivalue field. |
| `mvdedup()` | `mvdedup(<mvfield>)` | Removes duplicates from a multivalue field. |
| `mvsort()` | `mvsort(<mvfield>)` | Sorts a multivalue field. |
| `mvfind()` | `mvfind(<mvfield>, <regex>)` | Returns the index of the first match. |
| `mvzip()` | `mvzip(<mv1>, <mv2>, <delimiter>)` | Zips two multivalue fields together. |
| `mvrange()` | `mvrange(<start>, <end>, <step>)` | Generates a multivalue field of numbers. |
| `split()` | `split(<string>, <delimiter>)` | Splits a string into a multivalue field. |

#### Eval Functions — Mathematical

| Function | Signature | Description |
|---|---|---|
| `abs()` | `abs(<number>)` | Returns the absolute value. |
| `ceil()` / `ceiling()` | `ceil(<number>)` | Rounds up to the nearest integer. |
| `floor()` | `floor(<number>)` | Rounds down to the nearest integer. |
| `round()` | `round(<number>, <decimals>)` | Rounds to the specified number of decimal places. |
| `log()` | `log(<number>, <base>)` | Returns the logarithm. |
| `ln()` | `ln(<number>)` | Returns the natural logarithm. |
| `exp()` | `exp(<number>)` | Returns e raised to the power. |
| `pow()` | `pow(<base>, <exponent>)` | Returns base raised to the exponent. |
| `sqrt()` | `sqrt(<number>)` | Returns the square root. |
| `pi()` | `pi()` | Returns the value of pi. |
| `random()` | `random()` | Returns a random integer. |
| `sigfig()` | `sigfig(<number>)` | Rounds to significant figures. |

#### Eval Functions — Informational / Cryptographic

| Function | Signature | Description |
|---|---|---|
| `isbool()` | `isbool(<value>)` | Returns true if the value is boolean. |
| `isint()` | `isint(<value>)` | Returns true if the value is an integer. |
| `isnum()` | `isnum(<value>)` | Returns true if the value is numeric. |
| `isstr()` | `isstr(<value>)` | Returns true if the value is a string. |
| `isnull()` | `isnull(<value>)` | Returns true if the value is null. |
| `isnotnull()` | `isnotnull(<value>)` | Returns true if the value is not null. |
| `typeof()` | `typeof(<value>)` | Returns the data type of the value. |
| `md5()` | `md5(<string>)` | Returns the MD5 hash. |
| `sha1()` | `sha1(<string>)` | Returns the SHA-1 hash. |
| `sha256()` | `sha256(<string>)` | Returns the SHA-256 hash. |
| `sha512()` | `sha512(<string>)` | Returns the SHA-512 hash. |

### How to identify functions in SPL

1. Scan all command arguments for tokens followed by `(...)`.
2. In `stats` / `timechart` / `chart` / `eventstats` / `streamstats` — the aggregation expressions (e.g., `count(src_ip)`, `avg(bytes)`) are functions.
3. In `eval` — everything on the right-hand side that uses `name(...)` syntax is a function. Functions can be nested: `if(isnull(x), coalesce(y, z), x)`.
4. In `where` — expressions may use functions: `where like(uri, "%admin%")`.
5. Subsearches may contain their own functions — extract recursively.
6. Do not count `by`, `AS`, `OUTPUT`, `WHERE`, `OVER` — these are keywords, not functions.

### Context engine result

Use function descriptions from `context_engine_result` (stored in Skill 1). Map each identified function to its description from the stored result.
- If a function has no entry in `context_engine_result`: store `"description": "no description available"`.

**Output:** Write each function as `{ "name": "<function>", "description": "<description>" }` into the `functions` array.

---

## Skill 6 — Summary Generation (Splunk)

> **Name:** `summary-generation`
> **Description:** Produces a detailed, self-contained, plain-language summary of the SPL query covering data sources, filters, transformations, aggregations, and output shape.
> **When to use:** After Skills 3–5 have completed extraction. Uses extracted `fields`, `commands`, `functions`, plus `query`, `parsed_json`, `metadata`, `usage_type`, and `datasource` as context. Writes to the `summary` field.

**Purpose:** Produce a detailed summary of the Splunk query in plain language.

### Input context

Use all of the following as context when generating the summary:
- `query` — the raw SPL string
- `parsed_json` — the parsed AST (if present)
- `fields` — extracted field names
- `commands` — extracted commands with descriptions
- `functions` — extracted functions with descriptions
- `metadata` — platform-specific metadata (e.g., `source`, `sourcetype`, `index`)
- `usage_type` — how the query is used (e.g., `dashboard`, `alert`)
- `datasource` — the data type (e.g., `log`, `metric`)

### Context engine result

Use the platform-specific documentation context from `context_engine_result` (stored in Skill 1) to enrich the summary with accurate descriptions of what each command and field does in Splunk.

### Splunk-specific context to incorporate

When generating the summary, use the following Splunk-specific patterns to enrich the description:

1. **Index and sourcetype** — If the query specifies `index=...` or `sourcetype=...`, describe what data source is being queried (e.g., "Searches the `main` index for events with sourcetype `access_combined`, which typically contains web server access logs").
2. **Base search filters** — Describe any keyword searches or field filters in the base search clause (e.g., "Filters for events where `status` is 500 and `action` is `blocked`").
3. **Pipeline stages** — Walk through each pipeline stage in order and explain what it does:
   - What data flows in.
   - What the command does to it.
   - What data flows out.
4. **Aggregations** — Describe the grouping and aggregation logic clearly (e.g., "Groups events by `src_ip` and computes the count and average `bytes` for each group").
5. **Computed fields** — Explain any `eval` expressions (e.g., "Computes a new field `duration` by subtracting `start_time` from `end_time` and converting to seconds").
6. **Regex extractions** — Explain `rex` patterns in human terms (e.g., "Extracts the `username` from the raw event using a regex that captures the word after `user=`").
7. **Lookups** — Describe what enrichment is being applied (e.g., "Enriches events by looking up `src_ip` in the `threat_intel` lookup table to get `threat_category` and `threat_score`").
8. **Time bucketing** — If `timechart`, `bucket`, or `span` is used, describe the time granularity (e.g., "Buckets events into 1-hour intervals").
9. **Subsearches** — Describe what the subsearch produces and how it feeds into the main pipeline.
10. **Output shape** — Describe the final output (e.g., "Produces a table with columns `_time`, `src_ip`, `count`, `avg_response_time`").

### Quality requirements

- Must be **self-contained** — a reader unfamiliar with SPL syntax must fully understand what the query does from the summary alone.
- Must **not** reproduce raw SPL syntax without explanation.
- Must **not** be a single sentence — provide sufficient detail to reflect the full query logic.
- Should be **structured** — describe the pipeline flow sequentially, covering each stage.

**Output:** Write the generated text into the `summary` field.

---

## Skill 7 — Intent Generation (Splunk)

> **Name:** `intent-generation`
> **Description:** Derives a concise, goal-oriented statement of the analytical or operational objective the SPL query achieves.
> **When to use:** After Skill 6 has generated the summary. Uses the summary, query structure, `usage_type`, and `datasource` as context. Writes to the `intent` field.

**Purpose:** Derive the analytical or operational goal from the Splunk query.

### Input context

- The `summary` produced by Skill 6
- The query structure (commands, fields, functions)
- `usage_type` and `datasource` values

### Context engine result

Use the intent pattern context from `context_engine_result` (stored in Skill 1) to match the query against known Splunk intent patterns and validate the derived intent before writing it.

### Splunk-specific intent patterns

| SPL pattern | Likely intent |
|---|---|
| `stats count by <field>` with security fields (`src_ip`, `action`, `signature`) | Identify top contributors to a security event type |
| `timechart count by status` on web access logs | Monitor HTTP status code distribution over time |
| `where <field> > <threshold>` followed by aggregation | Detect anomalies or threshold breaches |
| `lookup threat_intel` + filter | Correlate events against threat intelligence |
| `transaction session_id` | Analyze user session behavior |
| `dedup` + `table` | Extract a unique list for investigation |
| `stats sum(bytes)` by `sourcetype` | Measure data volume for capacity planning |
| `eval` creating severity/risk fields | Classify or score events for triage |

### Quality requirements

- **One to three sentences** maximum.
- Must be **actionable and goal-oriented** — not a restatement of the summary.
- If the intent is ambiguous, state the most likely interpretation and **note the ambiguity** explicitly.

**Output:** Write the generated text into the `intent` field.

---

## Skill 8 — Use Case Generation (Splunk)

> **Name:** `use-case-generation`
> **Description:** Identifies the specific operational or business scenario the SPL query serves — e.g., security monitoring, APM, compliance reporting.
> **When to use:** After Skill 7 has generated the intent. Uses the intent, extracted fields/commands/functions, `usage_type`, and `datasource` as context. Writes to the `use_case` field.

**Purpose:** Identify the operational or business scenario the Splunk query serves.

### Input context

- The `intent` produced by Skill 7
- Extracted `fields`, `commands`, `functions` and their descriptions
- `usage_type` and `datasource` values

### Context engine result

Use the use case classification context from `context_engine_result` (stored in Skill 1) to classify the query against known Splunk use case patterns and validate the identified scenario before writing it.

### Splunk-specific use case indicators

| Indicator | Use case domain |
|---|---|
| `index=security` or `sourcetype=syslog` with `src_ip`, `dest_ip`, `action` | **Security monitoring** — detecting threats, intrusions, or policy violations |
| `sourcetype=access_combined` with `status`, `uri`, `response_time` | **Web application monitoring** — tracking HTTP errors, latency, and request patterns |
| `sourcetype=perfmon` or metric fields (`cpu`, `memory`, `disk`) | **Infrastructure monitoring** — server health and capacity tracking |
| `index=_internal` with `sourcetype`, `bytes`, ingestion fields | **Splunk administration** — monitoring ingestion volume, license usage, indexer health |
| `lookup` with threat-related tables | **Threat intelligence correlation** — enriching events with known-bad indicators |
| `transaction` or session-based grouping | **User behavior analytics** — session analysis, access pattern investigation |
| `usage_type: alert` + threshold logic | **Alerting and incident response** — real-time detection of critical conditions |
| `usage_type: dashboard` + `timechart` | **Operational dashboards** — continuous visual monitoring of KPIs |
| `usage_type: report` + `stats` aggregation | **Compliance and reporting** — periodic summarization for audit or management review |

### Quality requirements

- Must be **specific** — identify the operational domain and describe the scenario, not just a generic label.
- Must **reference** `usage_type` and `datasource` in context where meaningful.
- Must describe **how the query result serves** the identified use case (e.g., "used to populate a security dashboard panel tracking brute-force login attempts").

**Output:** Write the generated text into the `use_case` field.
