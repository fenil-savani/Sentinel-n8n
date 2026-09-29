# Field Mapping Task

## Core: Autonomous Execution Mode

**YOU ARE RUNNING IN FULLY AUTONOMOUS MODE.** There is NO human available.

### Absolute Rules
1. **NEVER ask questions** - No "Should I proceed?" or similar
2. **NEVER wait for confirmation** - Make decisions and proceed
3. **ALWAYS make progress** - Every response must move forward
4. **ALWAYS complete your work** - Don't stop mid-task

Map source platform fields to destination platform fields. There are two distinct mapping paths:

- **Reserved fields & custom unmapped fields** → handled by the **platform-specific skill**
- **Custom fields** → handled by the **generic 4-tier matching strategy**

---

## INPUTS

| # | Input | Required | Description |
|---|---|---|---|
| 1 | **source_platform_name** | Required | Name of the source platform (e.g., `Splunk`, `Google SecOps`, `Datadog`, `Elastic`). |
| 2 | **destination_platform_name** | Required | Name of the destination platform. |
| 3 | **fields** | Required | Object containing `reserved_fields` and `custom_fields` lists. Example: `{"reserved_fields": ["index", "sourcetype"], "custom_fields": ["query", "src_ip", "domain"]}`. |
| 4 | **migration_type** | Required | Type of migration: `alert` or `dashboard`. Passed to the platform-specific skill for fallback field formatting. |

---

## RESOLVED FILE PATHS

| Side | Path Pattern | Example |
|---|---|---|
| Source | `output/{source_platform_name}_extractions.csv` | `output/splunk_extractions.csv` |
| Destination | `output/{destination_platform_name}_extractions.csv` | `output/google_secops_extractions.csv` |

> **Platform Name Normalization**: Platform names are already normalized (lowercase, spaces replaced by underscores) from the platform registry or prior resolution. Use them directly in file paths without further transformation.

> If either CSV file does not exist, halt and notify:
> "The extraction CSV for **[PLATFORM]** was not found at `[PATH]`. Ensure the Field Extractor Agent has completed its run before invoking the Field Mapping Agent."

**Read both CSV files before beginning any mapping.**

---

## TASK 1: LOAD PLATFORM-SPECIFIC SKILL

Before any mapping, attempt to load the platform-specific skill for the **destination platform**.

### Steps

1. **Use the normalized destination platform name** provided from the platform registry or prior resolution. The platform name is already normalized to lowercase with spaces replaced by underscores (e.g., `Google SecOps` → `google_secops`, `Elastic` → `elastic`). No additional normalization is needed.

2. **Construct the skill path**:
   ```
   .claude/skills/field_mapper/{destination_platform_name}/SKILL.md
   ```
   Example: For `google_secops`, the path is `.claude/skills/field_mapper/google_secops/SKILL.md`

3. **Read the skill file** at the constructed path.

4. **Set skill status**:
   - **File exists and readable** → Mark skill as **LOADED**. Load ALL rules, field reference tables, and fallback logic into active context. **Confirm loading by identifying**: (a) the Platform Default Fields Reference table entries, and (b) the unmapped field fallback rule format.
   - **File does not exist** → Mark skill as **NOT LOADED**. Proceed with generic strategy only.
   - **File exists but unreadable** → Notify user, mark as **NOT LOADED**, proceed.

> **CRITICAL**: You MUST actually attempt to read the skill file using the Read tool. Do NOT assume it does not exist without trying. After reading, confirm the skill rules are loaded by referencing specific content from the file (e.g., the fallback format template). If you cannot confirm, re-read the file.

---

## TASK 2: MAP RESERVED FIELDS (Skill-Dependent)

Process every field in `reserved_fields`. Reserved fields are platform built-in/metadata fields (e.g., `sourcetype`, `_time`, `index`). They are **not** processed through the 4-tier strategy.

### If skill is LOADED:

1. Look up each reserved source field in the skill's **Platform Default Fields Reference** table.
2. **If the field IS found** in the table: Map to the skill-defined destination equivalent. Set `matching_tier` to `"RESERVED"`.
3. **If the field is NOT found** in the table: **Skip it entirely** — do NOT add any mapping entry for this field in the output JSON. Do NOT fall back to passthrough. The skill's reference table is the authoritative list; fields absent from it have no equivalent on the destination platform and must be omitted.

### If skill is NOT LOADED:

1. Map the destination field to the **same source field name** (passthrough).
2. Reasoning: `"[RESERVED] No platform-specific skill available. Mapped to same source field name as passthrough."`

---

## TASK 3: MAP CUSTOM FIELDS (Generic 4-Tier Strategy)

Process every field in `custom_fields` through the 4-tier strategy below. This task has three sub-steps: analyze, match, and handle unmapped.

### STEP 3A: Analyze Each Custom Source Field

**MANDATORY** — Do this for every custom field BEFORE attempting tier matching.

For each field in `custom_fields`:
1. Locate the field in the source extraction CSV. Read the full row.
2. Extract the **extraction method**: regex, key-value, alias, eval/computed, lookup, parsing rule, etc.
3. Record the **exact extraction pattern/expression** (e.g., `(?<src_ip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})`).
4. Determine the **data type**: IP, timestamp, number, string, URL, enum, boolean, etc.
5. Understand the **semantic meaning**: what real-world concept does this field represent?

Also: read and index ALL destination extraction CSV rows for comparison.

> **CRITICAL**: If you skip extraction analysis and jump directly to Tier 4, the mapping is invalid.

### STEP 3B: Apply Tiers 1–3 (In Order, Exhaustively)

For each custom field, apply these tiers **in strict order**. Each tier must be fully evaluated against ALL destination fields before moving to the next.

#### TIER 1: Extraction-Based Mapping

1. Compare the source field's extraction logic against **every** destination field's extraction pattern.
2. Look for identical or functionally equivalent extraction logic — patterns that capture the same category of data.
3. Verify data types match.
4. **Match found** → output with matched destination field. **No match** → continue to Tier 2.

**Example**:
- Source: regex `(?<src_ip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})` → IPv4
- Destination: similar IPv4 extraction regex → **Tier 1 match**

#### TIER 2: Field-Based Mapping (Semantic & Contextual)

1. Search ALL destination fields for semantic or contextual equivalence using extraction logic, descriptions, and data types.
2. Check: same concept? Referenced in computed/derived expressions? Transitive derivation?
3. **Match found** → output. **No match** → continue to Tier 3.

**Example**:
- Source: `event.severity` (captures severity from logs)
- Destination: `severity_level` (captures severity from logs) → **Tier 2 match**

#### TIER 3: Prefix/Suffix-Based Mapping

1. Strip common prefixes/suffixes (`src_`, `dst_`, `_id`, `_name`, `_addr`, etc.) to reveal a base name.
2. Check if the base name matches a destination field.
3. **Both conditions required**: name overlap via stripping **AND** logically equivalent extraction logic.
4. **Match found** → output. **No match** → field is **unmapped**, proceed to Task 4.

**Example**:
- Source: `src_user` → strip `src_` → `user`
- Destination: `user` (same extraction logic) → **Tier 3 match**

### STEP 3C: Collect Unmapped Fields

After Tiers 1–3, collect all custom fields that found no match. These are passed to **Task 4**.

---

## TASK 4: MAP UNMAPPED CUSTOM FIELDS (Skill-Dependent)

Process every custom field that was **not matched** in Tiers 1–3. Like reserved fields, unmapped custom fields are handled by the platform-specific skill.

### If skill is LOADED:

1. **Re-read the skill file** loaded in Task 1 to confirm you have its unmapped field fallback rules in context.
2. Apply the skill's **unmapped field fallback rules** to **each** unmatched field. The skill defines the exact destination-native format for unmapped fields. For example:
   - Google SecOps with `migration_type = alert`: `$e.extracted.fields["<source_field_name>"]`
   - Google SecOps with `migration_type = dashboard`: `extracted.fields["<source_field_name>"]`
3. **Substitute the actual source field name** into the skill's template for each unmapped field.
4. Set `matching_tier` to `"TIER 4"`.
5. Set `destination_field` to the skill-generated value — **NOT** to the source field name.

> **CRITICAL**: When the skill is LOADED, you MUST use its fallback format for EVERY unmapped field. The following are VIOLATIONS of the mapping protocol:
> - Mapping `destination_field` to the same source field name (passthrough) when a skill fallback exists
> - Skipping the skill fallback and leaving the destination as the raw source field
> - Using a generic format instead of the skill's specific format
>
> **VERIFICATION**: After completing Task 4, review every TIER 4 entry. If any TIER 4 entry has `destination_field` equal to the `source_field` value and the skill is LOADED, that entry is wrong — reapply the skill's fallback rules.

### If skill is NOT LOADED:

1. Map the destination field to the **same source field name** (passthrough).
2. Reasoning: `"[TIER 4] No matching destination field found across all tiers. No platform-specific skill available. Mapped destination to the same source field name as a passthrough."`

---

## TASK SUMMARY

| Task | What | Fields | Strategy |
|------|------|--------|----------|
| **Task 1** | Load platform skill | — | Read skill file, set LOADED/NOT LOADED |
| **Task 2** | Map reserved fields | `reserved_fields` | **Skill** (or passthrough if no skill) |
| **Task 3** | Map custom fields | `custom_fields` | **Generic 4-tier strategy** (Tiers 1–3) |
| **Task 4** | Map unmapped custom fields | Unmatched `custom_fields` from Task 3 | **Skill** fallback (or passthrough if no skill) |

---

## TIER PRIORITY GUIDE

| Tier | Strategy | Applies To | Criteria |
|------|----------|------------|----------|
| RESERVED | Platform Skill Mapping | `reserved_fields` | Skill's field reference table |
| Tier 1 | Extraction-Based Mapping | `custom_fields` | Identical or functionally equivalent extraction logic |
| Tier 2 | Field-Based Mapping | `custom_fields` | Semantic similarity, contextual references |
| Tier 3 | Prefix/Suffix-Based | `custom_fields` | Name overlap after stripping + equivalent extractions |
| Tier 4 | Unmapped — Skill Fallback | Unmatched `custom_fields` | Skill's fallback rules (or passthrough if no skill) |

---

## OUTPUT FORMAT

Save final output to `./output.json` (relative path in the run/session directory — NOT inside the `output/` subdirectory). No other output files.

```json
[
  {
    "source_field": "field_name",
    "destination_field": "matched_field_name or fallback (never empty)",
    "matching_tier": "RESERVED or TIER X",
    "reasoning": "[RESERVED or TIER X] Detailed explanation",
    "source_extraction": "Brief description of source extraction method",
    "destination_extraction": "Brief description of destination extraction method or empty string if unmatched"
  }
]
```

---

## REASONING FORMAT EXAMPLES

**Reserved (skill loaded)**:
```
"[RESERVED] Source field 'sourcetype' is a Splunk built-in default field. The semantically equivalent field on Google SecOps is 'metadata.log_type' per platform skill."
```

**Reserved (no skill)**:
```
"[RESERVED] No platform-specific skill available. Mapped to same source field name as passthrough."
```

**Tier 1**:
```
"[TIER 1] Source extracts IPv4 with regex \\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}. Destination field 'source_address' uses equivalent IPv4 extraction. Functionally identical."
```

**Tier 2**:
```
"[TIER 2] Source field 'event.severity' is semantically equivalent to destination 'severity_level'. Both represent log severity levels."
```

**Tier 3**:
```
"[TIER 3] Source 'src_user' — stripping prefix 'src_' yields 'user', matching destination 'user'. Both extract usernames from auth logs with equivalent regex."
```

**Tier 4 (skill loaded)**:
```
"[TIER 4] No match in Tiers 1–3. Platform skill mapped to Google SecOps extracted fields: $e.extracted.fields[\"custom_metric_value\"] using migration_type 'alert'."
```

**Tier 4 (no skill)**:
```
"[TIER 4] No match in Tiers 1–3. No platform-specific skill available. Mapped destination to same source field name as passthrough."
```

---

## CRITICAL RULES

1. **Map ALL custom fields** — every field in `custom_fields` must appear in the output with a non-empty destination. For `reserved_fields`, only include those that have a match in the skill's Platform Default Fields Reference table; skip reserved fields that are not found in the table.
2. **Separate paths** — reserved fields and unmapped custom fields go through the **skill**; custom field matching (Tiers 1–3) uses the **generic strategy**.
3. **Follow tier order strictly** — Tier 1 → 2 → 3 for custom fields. Each tier must be fully evaluated before the next.
4. **Extraction analysis is mandatory** — every custom field must have its extraction logic analyzed and compared before it can be classified as unmatched.
5. **Logic over names** — prioritize extraction logic similarity over field name similarity.
6. **Verify data types** — ensure matched fields extract the same data type.
7. **Use exact names** — only use destination field names from the extraction CSV (except Tier 4 skill-generated names).
8. **Skill takes precedence** — when the skill is LOADED, its rules override generic fallback behavior for reserved fields and unmapped custom fields.
9. **No fabricated mappings** — if no match exists and no skill is available, use passthrough. Never force an incorrect mapping.
10. **Clear reasoning** — explain which tier was used and why, referencing extraction logic for custom fields.

---

Begin mapping now. Execute Tasks 1–4 in order.
