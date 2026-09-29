# Supervisor Review — supervisor.md

> **File:** `agents/supervisor.md`  
> **Role:** Pipeline orchestrator — delegates to Generator and Reviewer, manages retry loop, assembles final output

---

## Purpose Alignment

| Goal | Status |
|---|---|
| Coordinate Generator → Reviewer retry loop | ✅ Implemented |
| Hard limit of 3 iterations | ✅ Implemented |
| Pass-through metadata to Phase 4 only | ✅ Implemented |
| Assemble temporal-format output.json | ✅ Implemented |
| Write output.json to runs directory | ✅ Implemented |
| Never modify source analysis fields between iterations | ✅ Constrained |

---

## Strengths

### 1. Information Routing — Correct Field Segmentation
The supervisor correctly segments which fields go to each agent:

- **Generator (Phase 1)** receives only translation-relevant fields:
  `source_platform`, `migration_type`, `destination_platform`, `destination_metadata`,
  `summary`, `intent`, `use_case`, `parsed_json`, `commands`, `fields`, `functions`,
  `field_mappings`, `iteration`, `validation_feedback`

- **Reviewer (Phase 2)** receives only validation-relevant fields:
  `generated_query`, `generation_output`, `summary`, `intent`, `use_case`,
  `field_mappings`, `source_platform`, `migration_type`, `destination_platform`,
  `destination_metadata`, `iteration`

- **Phase 4 only** receives the pass-through metadata fields.

This prevents context bloat and token waste on every subagent invocation.

### 2. Retry Loop Structure
The `WHILE current_iteration <= 3` loop with explicit STEP 1→2→3→4 flow is clear and
deterministic. The termination conditions are exhaustive:
- `PASS` → exits on success
- `FAIL + iter < 3` → continues with feedback
- `FAIL + iter >= 3` → exits with best result

No infinite loop is possible.

### 3. Dual Validation Report in Phase 4
Both `validation_report` and `platform_validation` are populated with identical content,
ensuring backward compatibility with consumers expecting either field name.

### 4. Feedback Block Construction
The supervisor builds structured `[{VALIDATOR_NAME} - FAILED]` feedback blocks that
reference the reviewer's explanations verbatim. The `Action required:` lines ensure
the Generator receives actionable, not vague, retry guidance.

### 5. `check_stop_conditions` Usage
Called at two points — after Phase 2 and at end of Phase 4 — creating a clear audit trail
of pipeline state transitions visible to the AgentWeave framework.

### 6. Non-Blocking Output Guarantee
The pipeline always produces `output.json` regardless of PASS/FAIL status:
- On FAIL: uses the last generated query with `confidence_score: 0.0`
- Constraints explicitly say "Never block the pipeline"

---

## Issues Found

### ISSUE-S1 — Phase 1 Delegation Hardcodes `iteration: 1` [CRITICAL]

**Location:** Phase 1 delegation JSON template

**Current:**
```json
{
  "task": "generate_destination_query",
  ...
  "iteration": 1,
  "validation_feedback": null
}
```

**Problem:** On retry iterations 2 and 3, this template is used as-is, sending `iteration: 1`
to the Generator every time. The Generator's Step 7 (retry feedback handling) is gated on
`iteration >= 2 AND validation_feedback is not null`. If `iteration` is always 1, the Generator
will skip Step 7 on every retry — never applying validation feedback.

**Fix Required:**
```json
{
  "task": "generate_destination_query",
  ...
  "iteration": "<current_iteration>",
  "validation_feedback": "<validation_feedback — null on first iteration, populated on retries>"
}
```

---

### ISSUE-S2 — Phase 2 Note References Old Knowledge Loading Pattern [MINOR]

**Location:** End of Phase 2 section

**Current:**
```
Note: The Reviewer loads platform knowledge files from `product_docs/` based on `migration_type`
— do not pass `platform_knowledge` in the delegation context.
```

**Problem:** After the platform-agnostic refactoring, the Reviewer now loads from
`product_docs/{destination_platform}/platform_config.md` and then follows the file path
from config to the query structure file. The note is technically still accurate but misleading
— it implies the Reviewer loads query structure files directly by `migration_type`, omitting
the platform_config indirection layer.

**Fix Required:** Update note to:
```
Note: The Reviewer loads platform knowledge via `product_docs/{destination_platform}/platform_config.md`
— do not pass `platform_knowledge` in the delegation context.
```

---

### ISSUE-S3 — Feedback Block Guidance References Stale File Name [MINOR]

**Location:** Phase 3 — Rules for constructing feedback

**Current:**
```
Reference the relevant platform knowledge file by name if applicable 
(e.g., "Refer to query_structure.md")
```

**Problem:** The example file name `query_structure.md` doesn't exist — the actual files are
`secops_rule_syntax.md` and `secops_query_syntax.md` (for google_secops), resolved via
platform_config.md. Agents following this guidance will reference a non-existent file.

**Fix Required:** Update to:
```
Reference the relevant platform knowledge file by name if applicable, using the path 
resolved from platform_config.md for the current destination_platform and migration_type.
```

---

### ISSUE-S4 — Constraints Item 7 Describes Old Knowledge Pattern [MINOR]

**Location:** Constraints section, item 7

**Current:**
```
Knowledge loading delegation: Platform knowledge files are loaded by each agent 
from `product_docs/` — do not load or pass them in delegation contexts
```

**Problem:** Generator no longer loads from `product_docs/` directly for translation knowledge —
it uses KAPA (`mcp__context-engine__context_engine_agent`) with a `product_docs/` fallback.
Reviewer loads from `platform_config.md` then follows config-driven file paths.
The description is partially correct but omits the KAPA layer.

**Fix Required:**
```
Knowledge loading delegation: Generator fetches platform knowledge via the KAPA context engine
(with product_docs/ as fallback). Reviewer loads platform_config.md from product_docs/.
Do not load or pass platform knowledge directly in delegation contexts.
```

---

### ISSUE-S5 — `iteration_count` Population Logic Not Explicit [LOW]

**Location:** Phase 4 final output field definitions

**Current:**
```
`iteration_count`: total number of Generator invocations completed
```

**Problem:** The output JSON template shows `"iteration_count": 1` as a static example.
The logic for setting this value from the loop variable `current_iteration` is not stated
explicitly. An LLM agent reading this may not connect `iteration_count` ← `current_iteration`.

**Fix Required:** Add explicit mapping:
```
`iteration_count`: Set to the value of `current_iteration` at loop exit 
(1 if passed on first attempt, 2 or 3 if retried)
```

---

## Output Correctness Verification

### Phase 4 JSON — Field Coverage

| Output Field | Source | Status |
|---|---|---|
| `destination_query` | Generator `result` | ✅ Mapped |
| `generation_notes` | Generator `reasoning` | ✅ Mapped |
| `reasoning` | Generator `reasoning` | ✅ Mapped (duplicate of generation_notes — intentional) |
| `analysis` | Generator `analysis` | ✅ Mapped |
| `assumptions` | Generator `assumptions` | ✅ Mapped |
| `warnings` | Generator `warnings` | ✅ Mapped |
| `confidence_score` | 1.0 (PASS) / 0.0 (FAIL) | ✅ Logic defined |
| `status` | Reviewer `overall_status` | ✅ Mapped |
| `iteration_count` | `current_iteration` at exit | ⚠️ Not explicitly stated |
| `validation_report` | Reviewer full output | ✅ Mapped |
| `platform_validation` | Same as validation_report | ✅ Mapped |
| `validation_errors` | List of failed validator names | ✅ Logic defined |
| `data_validation` | Always `null` | ✅ Defined |
| All pass-through fields | agent_input.* | ✅ Listed |

### output.json Completeness Score: **14/15 fields explicitly sourced** (iteration_count implicit)

---

## Summary

| Category | Count |
|---|---|
| Strengths identified | 6 |
| Issues found | 5 |
| Critical issues | 1 (ISSUE-S1: iteration always 1) |
| Minor issues | 3 |
| Low-priority issues | 1 |

**Overall rating: GOOD with one critical fix required.**  
The supervisor's orchestration logic is sound and well-structured. The critical bug (hardcoded
`iteration: 1`) will prevent retry behavior from working correctly. All other issues are
clarifications or documentation updates.
