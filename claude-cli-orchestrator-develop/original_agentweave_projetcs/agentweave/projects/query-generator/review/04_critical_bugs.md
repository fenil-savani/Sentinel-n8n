# Critical Bugs & Fixes — Query Generator Pipeline

> **Purpose:** Consolidated list of all actionable issues found across the three agent files,  
> ranked by severity. Fix in order — CRITICAL bugs will break the pipeline on every run.

---

## Severity Legend

| Level | Meaning |
|---|---|
| 🔴 CRITICAL | Pipeline produces wrong output on every run |
| 🟠 HIGH | Significant correctness issue, triggers on common inputs |
| 🟡 MINOR | Incorrect documentation, no runtime impact |
| 🔵 LOW | Cosmetic or edge-case gap |

---

## 🔴 CRITICAL — BUG-1: `field_mappings` Format Mismatch in Generator + Reviewer

**Affects:** `generator.md` (Step 4, Hard Constraint 2), `reviewer.md` (Input Contract, V2, V3)

**Root Cause:** The temporal-input.json format uses `field_mappings` as a **list of dicts**:
```json
[
  {"source_field": "EventCode", "destination_field": "metadata.product_event_type", "matching_tier": "TIER 1", ...},
  {"source_field": "user", "destination_field": "principal.user.userid", "matching_tier": "TIER 1", ...}
]
```
But multiple sections in both agent files describe the format as a **dict of strings** (old format):
```
{source_field: "dest_field"}
```

**Consequence:** Both Generator (Step 4) and Reviewer (V2, V3) will misread field_mappings
on every real pipeline execution, producing incorrect field translation and incorrect validation results.

### Fix — generator.md Step 4

**Find:**
```
For each entry in `field_mappings` (format: {src_field: "dest_field"} — single string value):
1. Identify where the source field is used in the query (filter, aggregation, group-by, output)
2. Replace with the destination field string from the mapping
```

**Replace with:**
```
For each entry in `field_mappings` (format: list of dicts — each dict has `source_field` 
and `destination_field` keys):
1. Extract `entry.source_field` to get the source field name
2. Extract `entry.destination_field` to get the destination field string
3. Identify where the source field is used in the query (filter, aggregation, group-by, output)
4. Replace with the destination field string from `entry.destination_field`
```

### Fix — generator.md Hard Constraint 2

**Find:**
```
Every source field in `field_mappings` must appear in `result` using its destination field 
string value, OR must be explicitly noted in `warnings`
```

**Replace with:**
```
Every entry in `field_mappings` (list of dicts) must have its `destination_field` value 
appear in `result`, OR the entry must be explicitly noted in `warnings` explaining why it was omitted.
```

### Fix — reviewer.md Input Contract

**Find:**
```
| `field_mappings` | `{source_field: "dest_field"}` — single string value per source field |
```

**Replace with:**
```
| `field_mappings` | List of dicts — each dict contains `source_field` and `destination_field` keys (plus `matching_tier`, `reasoning`, etc.) |
```

### Fix — reviewer.md V2 "What to Check"

**Find:**
```
For each entry in `field_mappings` (format: `{source_field: "dest_field"}` — single string value):
1. Check whether the destination field string appears in `generated_query`
```

**Replace with:**
```
For each entry in `field_mappings` (format: list of dicts):
1. Extract `entry.source_field` (source field name) and `entry.destination_field` (destination string)
2. Check whether `entry.destination_field` appears in `generated_query`
```

### Fix — reviewer.md V3 "What to Check" Step 1

**Find:**
```
1. Collect ALL destination field values from `field_mappings` (each entry's string value)
```

**Replace with:**
```
1. Collect ALL destination field strings by iterating `field_mappings` as a list of dicts 
   and extracting `entry.destination_field` from each entry
```

### Fix — reviewer.md V2 failure output example

**Find:**
```json
"ComputerName → [principal.hostname] — none found in query",
"LogonType → [network.direction, security.logon_type] — none found in query"
```

**Replace with:**
```json
"ComputerName → principal.hostname — not found in query",
"LogonType → network.direction — not found in query"
```

---

## 🔴 CRITICAL — BUG-2: Supervisor Phase 1 Hardcodes `iteration: 1` on All Iterations

**Affects:** `supervisor.md` — Phase 1 delegation JSON template

**Root Cause:** The Phase 1 JSON template has a static `"iteration": 1`:
```json
{
  "task": "generate_destination_query",
  ...
  "iteration": 1,
  "validation_feedback": null
}
```

**Consequence:** On iteration 2 and 3, the Generator receives `iteration: 1`. The Generator's
Step 7 (retry feedback handling) is gated on `iteration >= 2`. With `iteration` always 1,
Step 7 is never entered — the Generator ignores `validation_feedback` on every retry.
The retry loop runs but produces the same query repeatedly.

### Fix — supervisor.md Phase 1 delegation JSON

**Find:**
```json
  "iteration": 1,
  "validation_feedback": null
```

**Replace with:**
```json
  "iteration": "<current_iteration>",
  "validation_feedback": "<validation_feedback — null on iteration 1, populated string on iterations 2–3>"
```

---

## 🟠 HIGH — BUG-3: V4 Logtype Injection Check Contradicts Generator Behavior

**Affects:** `reviewer.md` — V4 "Logtype Injection Check" subsection

**Root Cause:** Generator Constraint 7 explicitly removes special logtype handling:
> `metadata.log_type` is NOT injected separately or treated as a priority condition.
> It is resolved through `field_mappings` the same as any other field.

But V4 still checks:
> Verify the logtype filter is the **FIRST** condition listed in that section.

**Consequence:** Every query where `destination_metadata.log_type` is non-empty will fail V4,
forcing unnecessary retries. The Generator cannot fix this — it is working as designed.

### Fix — reviewer.md V4 "Logtype Injection Check"

**Find and remove the entire "Logtype Injection Check" subsection** (roughly 6 lines):
```
#### Logtype Injection Check
If `destination_metadata.log_type` is non-empty:
- Find the events/filter section (or equivalent for the platform)
- Verify the logtype filter is the FIRST condition listed in that section
- The correct logtype filter format is in the `platform_config.md` "Logtype Filter Format" section
- Consult the loaded `query_structure` file for additional logtype injection guidance
```

**Replace with:**
```
#### Logtype Field Coverage Check
If `destination_metadata.log_type` is non-empty:
- Verify the logtype value appears somewhere in the generated query
- Position is NOT mandated — logtype is a regular field_mapping entry, not a special first condition
- Use V2/V3 field_mappings checks to verify the destination logtype field is present
```

---

## 🟡 MINOR — BUG-4: Generator `analysis` Output Example Contains Stale Logtype Reference

**Affects:** `generator.md` — Output Format section, `analysis` field example

**Find:**
```
(6) logtype injected as first events: condition, (7) validation feedback addressed: [if retry, list each fix made]
```

**Replace with:**
```
(6) platform_config loaded: destination_query_language resolved, field_mappings iterated as list of dicts, (7) validation feedback addressed: [if retry, list each fix made]
```

---

## 🟡 MINOR — BUG-5: Generator System Context References Removed `normalized_json`

**Affects:** `generator.md` — System Context section

**Find:**
```
The source query's meaning, intent, and structure are fully captured in `normalized_json`.
```

**Replace with:**
```
The source query's meaning, intent, and structure are fully captured in the flat temporal-format
input fields: `summary`, `intent`, `use_case`, `parsed_json`, `commands`, `fields`, `functions`.
```

---

## 🟡 MINOR — BUG-6: Supervisor Phase 2 Note Describes Old Knowledge Loading Pattern

**Affects:** `supervisor.md` — end of Phase 2 section

**Find:**
```
Note: The Reviewer loads platform knowledge files from `product_docs/` based on `migration_type`
— do not pass `platform_knowledge` in the delegation context.
```

**Replace with:**
```
Note: The Reviewer loads platform knowledge via `product_docs/{destination_platform}/platform_config.md`
— do not pass `platform_knowledge` in the delegation context.
```

---

## 🟡 MINOR — BUG-7: Supervisor Phase 3 Feedback References Non-Existent File Name

**Affects:** `supervisor.md` — Phase 3, Rules for constructing feedback

**Find:**
```
Reference the relevant platform knowledge file by name if applicable 
(e.g., "Refer to query_structure.md")
```

**Replace with:**
```
Reference the relevant platform knowledge file by its config-resolved path if applicable
(e.g., "Refer to the query structure file at the path listed in platform_config.md for 
migration_type='rule'")
```

---

## 🟡 MINOR — BUG-8: Reviewer V4/V5 Missing Explicit Read Tool Call Instructions

**Affects:** `reviewer.md` — Execution Workflow Steps 4 and 5, Platform Config Loading

**Issue:** The reviewer is told to "load" and "compare against" files but no explicit
`Read` tool call instruction is given. Add to Platform Config Loading section:

**Find:**
```
Before running any validators, load the platform configuration for the destination platform:
File path: `product_docs/{destination_platform}/platform_config.md`
```

**Prepend:**
```
CRITICAL: Before running any validators, call the `Read` tool with:
File path: `product_docs/{destination_platform}/platform_config.md`
```

---

## 🔵 LOW — BUG-9: Generator Hard Constraint Numbering Gap (Missing Constraint 3)

**Affects:** `generator.md` — Hard Constraints section

**Issue:** Constraint 3 was deleted. Renumber:
- Old 4 → New 3
- Old 5 → New 4
- Old 6 → New 5
- Old 7 → New 6

---

## 🔵 LOW — BUG-10: Generator `source_sourcetype` Dead Field in Input Contract

**Affects:** `generator.md` — Input Contract table

**Issue:** `source_sourcetype` is listed with instructions to "extract from parsed_json if needed"
but is never referenced in any generation step. Remove this row from the Input Contract table.

---

## Fix Priority Order

| Priority | Bug | File | Effort |
|---|---|---|---|
| 1 | BUG-1: field_mappings format (generator Step 4 + Hard Constraint 2) | `generator.md` | ~10 min |
| 2 | BUG-1: field_mappings format (reviewer Input Contract + V2 + V3) | `reviewer.md` | ~10 min |
| 3 | BUG-2: Phase 1 hardcodes iteration: 1 | `supervisor.md` | ~2 min |
| 4 | BUG-3: V4 logtype injection check contradicts generator | `reviewer.md` | ~5 min |
| 5 | BUG-4: Stale logtype reference in analysis example | `generator.md` | ~2 min |
| 6 | BUG-5: normalized_json reference in System Context | `generator.md` | ~2 min |
| 7 | BUG-6: Phase 2 note — old knowledge loading pattern | `supervisor.md` | ~2 min |
| 8 | BUG-7: Phase 3 feedback references non-existent filename | `supervisor.md` | ~2 min |
| 9 | BUG-8: Missing Read tool call instruction in reviewer | `reviewer.md` | ~3 min |
| 10 | BUG-9: Constraint numbering gap | `generator.md` | ~1 min |
| 11 | BUG-10: Dead source_sourcetype field | `generator.md` | ~1 min |

---

## Summary Table

| File | Critical | High | Minor | Low | Total |
|---|---|---|---|---|---|
| `supervisor.md` | 1 (BUG-2) | 0 | 2 (BUG-6, 7) | 0 | 3 |
| `generator.md` | 1 (BUG-1 partial) | 0 | 2 (BUG-4, 5) | 2 (BUG-9, 10) | 5 |
| `reviewer.md` | 1 (BUG-1 partial) | 1 (BUG-3) | 1 (BUG-8) | 0 | 3 |
| **TOTAL** | **2** | **1** | **5** | **2** | **11** |

**Fix BUG-1 and BUG-2 before any pipeline test run. Fix BUG-3 before any run with non-empty logtype.**
