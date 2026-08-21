# Generator Review — generator.md

> **File:** `agents/generator.md`  
> **Role:** Expert query migration engineer — translates normalized source query representation  
> into a syntactically correct and semantically complete destination platform query  
> **Tools available:** `Read`, `Grep`, `mcp__context-engine__context_engine_agent`

---

## Purpose Alignment

| Goal | Status |
|---|---|
| Accept temporal-format flat input (no `normalized_json` wrapper) | ✅ Implemented |
| Fetch platform knowledge via KAPA before generation | ✅ Implemented (Step 0-PRE + Step 0) |
| Translate all field_mappings into destination query | ✅ Specified |
| Apply retry feedback when `iteration >= 2` | ✅ Specified (Step 7) |
| Return structured JSON (`analysis`, `reasoning`, `assumptions`, `warnings`, `result`) | ✅ Specified |
| Never write output.json (Supervisor owns it) | ✅ Clearly stated |
| No logtype special-casing | ✅ Constraint 7 added |

---

## Strengths

### 1. KAPA Integration — Well-Structured 5-Section Query
The KAPA query template is organized into five purposeful sections:
- **SECTION 1**: Syntax reference (structure, required/optional sections)
- **SECTION 2**: Command and function mappings for the actual commands used
- **SECTION 3**: Aggregation and time window patterns
- **SECTION 4**: Limitations and workarounds (scoped to extracted commands)
- **SECTION 5**: Use-case-specific detection example

This ensures the LLM gets targeted, relevant context rather than generic platform docs.

### 2. Dynamic Command Scoping in KAPA Query
SECTION 2 and SECTION 4 use `{commands[].name}` as a filter — only asking for mappings
for the commands actually present in the source query. This prevents the LLM from receiving
and applying irrelevant command mappings.

### 3. Step 0-PRE — Platform Config Pre-load
Reading `platform_config.md` before constructing the KAPA query ensures the query language
name (`YARA-L 2.0`, or any future platform's language) is dynamically resolved, not hardcoded.

### 4. Fallback Strategy for KAPA Failure
If KAPA fails or returns empty content, the agent falls back to reading `product_docs/` directly
and documents the fallback in `warnings`. The pipeline is never blocked.

### 5. Step-by-Step Generation Process (Steps 1–8)
The eight-step process enforces a disciplined translation sequence:
1. Understand detection objective
2. Analyze source query structure
3. Map commands/functions
4. Apply field mappings
5. Construct query
6. Apply edge cases
7. Handle retry feedback
8. Self-verify

This reduces the chance of the LLM skipping critical translation steps.

### 6. Hard Constraints Prevent Common Failures
Seven hard constraints explicitly prohibit:
- Hallucinating field names, operators, or syntax
- Dropping field_mappings entries without documentation
- Returning non-JSON output
- Partial or placeholder queries
- Ignoring retry feedback
- Special logtype handling

### 7. Prompting Guidance with Explicit Reasoning Examples
Three concrete reasoning examples (aggregation, field mapping, edge case) show the
LLM the expected reasoning pattern — increasing output consistency.

---

## Issues Found

### ISSUE-G1 — field_mappings Format Description Is Wrong [CRITICAL]

**Location:** Input Contract, Step 4, Hard Constraint 2

**Current (Input Contract):**
```
| `field_mappings` | list of dicts | `[{source_field, destination_field, matching_tier, ...}]`
```
This part is correct.

**Current (Step 4):**
```
For each entry in `field_mappings` (format: {src_field: "dest_field"} — single string value):
```
**This is WRONG.** Step 4 describes the old format (dict mapping source → string destination).
The actual format is a list of dicts, each with `source_field` and `destination_field` keys.

**Current (Hard Constraint 2):**
```
Every source field in `field_mappings` must appear in `result` using its destination field 
string value
```
The wording "string value" is ambiguous — could be misread as accessing dict as `field_mappings["source_field"]`.

**Impact:** When the LLM reads Step 4 and tries to iterate `field_mappings` as `{src: dest_string}`,
it will fail to find any entries because the actual data structure is `[{source_field: "ComputerName", destination_field: "principal.hostname", ...}]`.

**Fix Required — Step 4:**
```markdown
For each entry in `field_mappings` (format: list of dicts — each dict has `source_field` 
and `destination_field` keys):
1. Access `entry.source_field` to get the source field name
2. Access `entry.destination_field` to get the destination field string
3. Identify where the source field is used in the query
4. Replace with the destination field string from `entry.destination_field`
```

---

### ISSUE-G2 — `analysis` Output Example Contains Stale Logtype Reference [MINOR]

**Location:** Output Format section — `analysis` field example string

**Current:**
```
"analysis": "Detailed step-by-step trace covering: ... (6) logtype injected as first events: 
condition, (7) validation feedback addressed: [if retry, list each fix made]"
```

**Problem:** Step `(6) logtype injected as first events: condition` was removed when logtype
special injection was eliminated. The analysis output example now describes behavior that
the Generator is explicitly forbidden from doing (Constraint 7).

**Impact:** LLM may include logtype-injection language in its analysis output, causing
confusion in downstream consumers and inconsistency with reviewer V4 behavior.

**Fix Required:** Replace `(6) logtype injected as first events: condition` with:
```
(6) platform_config loaded: destination_query_language = {value}, 
    all field_mappings traversed as list of dicts
```

---

### ISSUE-G3 — Hard Constraint Numbering Gap [LOW]

**Location:** Hard Constraints section

**Current numbering:** 1, 2, 4, 5, 6, 7  
**Missing:** Constraint 3

**Problem:** Constraint 3 was deleted (it was the mandatory logtype constraint). The gap
in numbering suggests missing content and may confuse readers/maintainers.

**Fix Required:** Renumber constraints 4→3, 5→4, 6→5, 7→6 to close the gap.

---

### ISSUE-G4 — Step 0-PRE Fallback Is Insufficiently Specific [LOW]

**Location:** Step 0-PRE — fallback instruction

**Current:**
```
If the file does not exist: Use `destination_platform` as a fallback label for the query 
language and proceed.
```

**Problem:** `destination_platform` is a value like `"google_secops"` — not a query language
name like `"YARA-L 2.0"`. Using it as `{destination_query_language}` in the KAPA query would
produce a malformed section header like `## SECTION 1 — google_secops rule Syntax Reference`,
which KAPA might misinterpret.

**Fix Required:**
```
If the file does not exist: Use the `destination_platform` value as a label in the KAPA
query template (e.g., `## SECTION 1 — google_secops rule Syntax Reference`). Document
the fallback in `warnings` output field.
```

---

### ISSUE-G5 — `source_sourcetype` Dead Field in Input Contract [LOW]

**Location:** Input Contract — Core Agent Inputs table

**Current:**
```
| `source_sourcetype` | string | Source sourcetype — not a top-level field in temporal format; 
if needed, extract from `parsed_json.base_search.criteria` |
```

**Problem:** This field is described as something to extract from `parsed_json` if needed,
but it is:
1. Not used in any of Steps 1–8
2. Not referenced in the KAPA query template
3. Not in the Step 8 self-verification checklist

It serves no purpose in the generation process and adds confusion to the input contract.

**Fix Required:** Remove `source_sourcetype` from the Input Contract table entirely.
The generator accesses `parsed_json.base_search.criteria` directly in Step 2.

---

### ISSUE-G6 — System Context Block References Removed `normalized_json` [MINOR]

**Location:** System Context section (below Step 0B)

**Current:**
```
You are translating a source query that has already been analyzed and normalized upstream. 
The source query's meaning, intent, and structure are fully captured in `normalized_json`.
```

**Problem:** The input format was migrated to temporal flat format. `normalized_json` no longer
exists — it was replaced by top-level flat fields (`summary`, `intent`, `use_case`, `parsed_json`,
`commands`, `fields`, `functions`).

**Fix Required:** Update to:
```
The source query's meaning, intent, and structure are fully captured in the flat temporal-format
input fields: `summary`, `intent`, `use_case`, `parsed_json`, `commands`, `fields`, `functions`.
```

---

## Input → Output Contract Verification

### Input Fields Used in Generation Steps

| Input Field | Used In | Status |
|---|---|---|
| `source_platform` | Step 0 KAPA params, Step 0-PRE path | ✅ |
| `destination_platform` | Step 0 KAPA params, Step 0-PRE path | ✅ |
| `migration_type` | Step 0 KAPA params, platform config lookup | ✅ |
| `destination_metadata` | Step 2, Step 5 (logtype via field_mappings) | ✅ |
| `summary` | Step 1, KAPA query | ✅ |
| `intent` | Step 1, KAPA query | ✅ |
| `use_case` | Step 1, KAPA query | ✅ |
| `parsed_json` | Step 2 (source query structure) | ✅ |
| `commands` | Step 3, KAPA query SECTION 2+4 | ✅ |
| `fields` | Step 2 (source fields referenced) | ⚠️ Used but no dedicated Step |
| `functions` | Step 3, KAPA query SECTION 2+4 | ✅ |
| `field_mappings` | Step 4 (field translation) | ⚠️ Format described incorrectly (ISSUE-G1) |
| `iteration` | Step 7 gate condition | ✅ |
| `validation_feedback` | Step 7 (retry fixes) | ✅ |

### Output Fields Required

| Output Field | Required | Generator Responsibility |
|---|---|---|
| `analysis` | ✅ | Generate step-by-step trace |
| `reasoning` | ✅ | High-level approach explanation |
| `assumptions` | ✅ | Interpretive decisions |
| `warnings` | ✅ | Gaps, approximations |
| `result` | ✅ | Complete executable query |

All 5 output fields are specified and well-described.

---

## Summary

| Category | Count |
|---|---|
| Strengths identified | 7 |
| Issues found | 6 |
| Critical issues | 1 (ISSUE-G1: field_mappings format) |
| Minor issues | 2 |
| Low-priority issues | 3 |

**Overall rating: GOOD with one critical fix required.**  
The generation logic is well-structured and disciplined. The KAPA integration and step-by-step
process provide a strong foundation for consistent query generation. The critical field_mappings
format bug must be fixed before the pipeline can correctly process temporal-format inputs.
