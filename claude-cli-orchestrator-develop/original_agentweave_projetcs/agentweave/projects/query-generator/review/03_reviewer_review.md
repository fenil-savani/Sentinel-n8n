# Reviewer Review — reviewer.md

> **File:** `agents/reviewer.md`  
> **Role:** Query validation expert — runs 5 independent validators (V1–V5) on every invocation  
> **Tools available:** `Read`, `Grep`, `mcp__google-secops-mcp-server__validate_dashboard_query`, `mcp__google-secops-mcp-server__validate_rule`

---

## Purpose Alignment

| Goal | Status |
|---|---|
| Run all 5 validators regardless of early failures | ✅ Enforced |
| V1: Semantic intent coverage check | ✅ Implemented |
| V2: Field mapping presence check | ✅ Implemented (format bug — see ISSUE-R1) |
| V3: Destination field coverage check | ✅ Implemented (format bug — see ISSUE-R1) |
| V4: Structural validation using platform knowledge | ✅ Implemented (config-driven) |
| V5: Syntax validation via MCP tool | ✅ Implemented (config-driven) |
| Return structured JSON with per-validator results | ✅ Specified |
| Build actionable `feedback_for_generator` on FAIL | ✅ Specified |
| Never generate or suggest an improved query | ✅ Constrained |
| Degraded mode for MCP unavailability | ✅ Implemented |

---

## Strengths

### 1. Five Independent Validators — No Short-Circuiting
The execution workflow explicitly requires running V1→V2→V3→V4→V5 in sequence regardless
of which validators pass or fail. This guarantees the Generator receives all failures in one
pass, enabling a single retry to fix everything rather than discovering failures one at a time.

### 2. V1 — Semantic Validator Design
V1 goes beyond syntactic checks and evaluates whether the detection intent is preserved:
- Event type/category filter present
- Aggregation logic preserved
- Threshold condition present
- Time window preserved
- Grouping dimension present

This catches translation failures that would pass all syntactic checks but produce a
fundamentally wrong query.

### 3. V2/V3 Distinction Is Architecturally Sound
- **V2** checks: "Is the destination field from field_mappings present in the query?"
- **V3** checks: "Is the destination field either in the query OR acknowledged in warnings?"

V3's allowance for Generator-documented omissions is critical — it prevents false failures
when a field legitimately has no destination equivalent and the Generator correctly documents it.

### 4. Pre-MCP Completeness Gate in V5
Before calling the MCP tool (which can be slow/expensive), the reviewer checks:
- Query is not empty or whitespace-only
- Query does not contain placeholder text (`<placeholder>`, `TODO`)
- Query is not truncated (`...` or mid-statement)

This avoids wasting MCP calls on obviously broken queries.

### 5. Platform Config Loading Section
The new "Platform Configuration Loading" section at the top correctly instructs the reviewer
to resolve all platform-specific values (MCP tool names, query structure file path, illegal
constructs) from `platform_config.md` before running any validators. This is the correct
location and order of operations.

### 6. Feedback Block Format Is Actionable
The `feedback_for_generator` format requires:
- Exact validator name in header `[V{N}: NAME - FAILED]`
- Exact explanation from the validator result
- Explicit `Action required:` line with concrete instruction

This structure ensures the Generator cannot misinterpret what to fix.

### 7. Quality Standards Section
The four quality standards (`Be Specific`, `Be Actionable`, `Be Complete`, `Be Fair`) provide
the LLM with concrete anti-patterns:
- ❌ "Field mapping issue found" vs ✅ "Expected field 'principal.hostname' (mapped from 'ComputerName') — not found anywhere"
- Explicitly tells the reviewer NOT to penalize documented Generator omissions (V3 fairness)

---

## Issues Found

### ISSUE-R1 — field_mappings Format Described Incorrectly in V2, V3, Input Contract [CRITICAL]

**Location:** Input Contract table, V2 "What to Check", V3 "What to Check"

**Current (Input Contract):**
```
| `field_mappings` | `{source_field: "dest_field"}` — single string value per source field |
```

**Current (V2 What to Check):**
```
For each entry in `field_mappings` (format: `{source_field: "dest_field"}` — single string value):
1. Check whether the destination field string appears in `generated_query`
```

**Current (V3 What to Check):**
```
Collect ALL destination field values from `field_mappings` (each entry's string value)
```

**Problem:** All three describe the OLD field_mappings format (dict of `{source: "dest_string"}`).
The actual temporal format is a **list of dicts**:
```json
[
  {
    "source_field": "EventCode",
    "destination_field": "metadata.product_event_type",
    "matching_tier": "TIER 1",
    "reasoning": "...",
    "source_extraction": "...",
    "destination_extraction": "..."
  }
]
```

**Impact:** The reviewer will attempt to iterate `field_mappings` as a dict, fail to find
the expected `"dest_field"` string values, and produce incorrect V2/V3 validation results
(either false passes or false fails depending on LLM interpretation).

**Fix Required — Input Contract:**
```
| `field_mappings` | List of dicts — each dict has `source_field` and `destination_field` keys |
```

**Fix Required — V2 What to Check:**
```
For each entry in `field_mappings` (format: list of dicts):
1. Extract `entry.source_field` (the source field name)
2. Extract `entry.destination_field` (the destination field string to look for)
3. Check whether `entry.destination_field` appears in `generated_query`
```

**Fix Required — V3 What to Check:**
```
1. Collect ALL destination field strings by iterating `field_mappings` as a list of dicts
   and extracting `entry.destination_field` from each entry
```

---

### ISSUE-R2 — V4 Logtype Injection Check Contradicts Generator Behavior [HIGH]

**Location:** V4 "What to Check" — Logtype Injection Check subsection

**Current:**
```
#### Logtype Injection Check
If `destination_metadata.log_type` is non-empty:
- Find the events/filter section (or equivalent for the platform)
- Verify the logtype filter is the FIRST condition listed in that section
```

**Problem:** Generator Constraint 7 explicitly states:
```
No special logtype handling: metadata.log_type is NOT injected separately or treated as a 
priority condition. It is resolved through field_mappings the same as any other field.
```

The Generator is instructed NOT to inject logtype first. V4 is instructed to CHECK that
logtype IS first. The generator will fail V4 on every run where `destination_metadata.log_type`
is non-empty — even when the query is correct.

**Fix Required:** Remove the "Logtype Injection Check" subsection from V4, or replace with:
```
#### Logtype Field Coverage Check
If `destination_metadata.log_type` is non-empty:
- Verify the logtype value appears somewhere in the generated query
- Position is NOT mandated — logtype is treated as a regular field_mapping entry
- Check that the destination field for logtype (from field_mappings) is present in the query
```

---

### ISSUE-R3 — V2 Failure Output Example Shows Old Format [MINOR]

**Location:** V2 "Output" section — failure example

**Current:**
```json
{
  "missing_field_mappings": [
    "ComputerName → [principal.hostname] — none found in query",
    "LogonType → [network.direction, security.logon_type] — none found in query"
  ]
}
```

**Problem:** The square brackets `[principal.hostname]` and `[network.direction, security.logon_type]`
suggest the old format where one source field mapped to multiple destinations. In the temporal
format, each `field_mappings` entry has exactly one `destination_field` string — so
`LogonType` with two destinations would be two separate list entries (two separate V2 failures).

**Fix Required:**
```json
{
  "missing_field_mappings": [
    "ComputerName → principal.hostname — not found in query",
    "LogonType → network.direction — not found in query"
  ]
}
```

---

### ISSUE-R4 — project.yaml Reviewer Tools Are Platform-Specific [ARCHITECTURE]

**Location:** `project.yaml` — reviewer subagent tools list

**Current:**
```yaml
tools: [Read, Grep, mcp__google-secops-mcp-server__validate_dashboard_query, 
        mcp__google-secops-mcp-server__validate_rule]
```

**Problem:** The reviewer's `platform_config.md` loading correctly resolves MCP tool names
dynamically — but the tools must be registered in `project.yaml` for the AgentWeave framework
to make them available to the agent. For a new destination platform (e.g., `microsoft_sentinel`),
the new MCP tools would need to be added to `project.yaml` before the reviewer can call them.

**Impact:** Platform-agnosticism is architecturally incomplete — `platform_config.md` tells
the reviewer WHICH tool to call, but the framework must have REGISTERED that tool for the
call to succeed. This is a project.yaml concern, not a reviewer.md concern — but it must
be addressed in the onboarding guide.

**Fix Required (in `_platform_onboarding.md`):** Add step:
```
Step 5b: Add new MCP tool names to project.yaml reviewer tools list
```

---

### ISSUE-R5 — Validator Execution Workflow Missing Explicit File Read Steps [MINOR]

**Location:** Execution Workflow — Steps 4 and 5

**Current:**
```
### Step 4: Execute V4 (Structure)
- Compare query structure against `query_structure` platform knowledge file
- Verify all required sections are present
```

**Problem:** The reviewer is told to "compare against query_structure" but the step does
not explicitly say to READ the file first (from the path resolved by platform_config.md).
An LLM agent executing this step-by-step may skip the Read tool call and try to recall
the file from memory.

**Fix Required — Step 4:**
```
### Step 4: Execute V4 (Structure)
1. READ the query structure file at the path stored in `query_structure_file` 
   (resolved from platform_config.md during Platform Configuration Loading)
2. Identify required and conditional sections from the file
3. Check each required section against `generated_query`
4. Check illegal constructs from `illegal_constructs` table against `generated_query`
```

---

### ISSUE-R6 — Platform Config Loading Has No Explicit Tool Call Instruction [MINOR]

**Location:** "Platform Configuration Loading" section

**Current:**
```
Before running any validators, load the platform configuration for the destination platform:
File path: `product_docs/{destination_platform}/platform_config.md`
```

**Problem:** The section tells the reviewer WHAT to load and WHAT to extract, but does not
explicitly say to use the `Read` tool. LLM agents benefit from explicit tool call instructions,
especially since this is a required first step.

**Fix Required:**
```
CRITICAL: Before running any validators, call the `Read` tool to load:
`product_docs/{destination_platform}/platform_config.md`
(substitute {destination_platform} with the value from agent input)
```

---

## Validator Coverage Matrix

| Validator | Type | Catches | Tool Required |
|---|---|---|---|
| V1 — Intent Coverage | Semantic | Missing detection logic, wrong event type, missing threshold/time window | None |
| V2 — Field Mapping | Deterministic | Destination field absent from query | None |
| V3 — Missing Fields | Deterministic | Destination field absent AND not in Generator warnings | None |
| V4 — Structure | Rule-based | Missing required sections, illegal constructs, logtype position | Read (platform config) |
| V5 — Syntax | MCP tool | Syntax errors in destination query language | MCP validation server |

**Gap Analysis:**
- No validator checks for **semantic correctness of aggregation math** (e.g., wrong threshold value)
- No validator checks **field_mappings coverage against the source query AST** (only checks destination fields are present, not that all source fields were found and mapped)
- V5 catch-all for syntax is good but depends on MCP availability

---

## Output Contract Verification

### Reviewer Output Structure

| Output Field | Required | Status |
|---|---|---|
| `overall_status` | ✅ | "PASS" or "FAIL" |
| `all_passed` | ✅ | Boolean |
| `iteration` | ✅ | Pass-through from input |
| `validation_results[]` | ✅ | Array of 5 validator results |
| `validation_results[].validator` | ✅ | Validator name string |
| `validation_results[].is_valid` | ✅ | Boolean |
| `validation_results[].explanation` | ✅ | Specific explanation |
| `validation_results[].details` | ✅ | Structured details (varies per validator) |
| `failed_validators` | ✅ | Array of failed validator names |
| `feedback_for_generator` | ✅ | Null on PASS, formatted string on FAIL |

All required output fields are defined and well-specified. **Output contract: COMPLETE.**

---

## Summary

| Category | Count |
|---|---|
| Strengths identified | 7 |
| Issues found | 6 |
| Critical issues | 1 (ISSUE-R1: field_mappings format) |
| High severity | 1 (ISSUE-R2: logtype injection contradiction) |
| Architecture concern | 1 (ISSUE-R4: project.yaml tools) |
| Minor issues | 3 |

**Overall rating: GOOD with two fixes required (critical + high).**  
The five-validator architecture is well-designed with clear pass/fail criteria and actionable
feedback. The critical field_mappings format bug and the logtype injection contradiction
must be fixed before the reviewer will produce correct results on temporal-format inputs.
