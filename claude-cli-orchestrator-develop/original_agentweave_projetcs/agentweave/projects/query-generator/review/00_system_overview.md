# System Overview Review — Destination Query Generator

> **Review Date:** 2026-04-10  
> **Reviewer:** AI Engineering Audit  
> **Scope:** All agent `.md` files + project.yaml + product_docs structure  
> **Goal alignment:** Convert normalized source queries → validated destination platform queries via a 3-agent multi-step pipeline

---

## System Architecture Summary

```
INPUT (temporal-input.json)
        │
        ▼
┌───────────────────────┐
│     SUPERVISOR        │  Orchestrator: controls retry loop, assembles final output
│     (supervisor.md)   │  Model: haiku
└───────┬───────────────┘
        │ delegates via Task tool
        ├─────────────────────────────────────────────────────┐
        ▼                                                     ▼
┌───────────────────────┐                       ┌────────────────────────┐
│     GENERATOR         │  ←── retry feedback   │      REVIEWER          │
│     (generator.md)    │  ──── generated_query ─►   (reviewer.md)       │
│     Model: haiku      │                       │   Model: haiku         │
│     Tools:            │                       │   Tools:               │
│       Read, Grep      │                       │     Read, Grep         │
│       mcp__context-   │                       │     mcp__google-secops │
│       engine__*       │                       │     __validate_rule    │
└───────────────────────┘                       │     __validate_dash*   │
                                                └────────────────────────┘
        │
        ▼
OUTPUT (output.json) — temporal-format, written by Supervisor
```

### Data Flow (per iteration)

```
agent_input → Supervisor → [summary, intent, use_case, parsed_json, commands,
                             fields, functions, field_mappings, destination_*]
                                        ↓
                              Generator (KAPA + platform_config)
                                        ↓
                         {analysis, reasoning, assumptions, warnings, result}
                                        ↓
                              Reviewer (platform_config + MCP)
                                        ↓
                         {overall_status, validation_results, feedback_for_generator}
                                        ↓
                     [PASS] → Phase 4 output assembly
                     [FAIL, iter < 3] → retry with feedback → Generator
                     [FAIL, iter = 3] → Phase 4 with best result + FAIL status
                                        ↓
                              output.json (temporal format)
```

---

## Overall Assessment

| Dimension | Rating | Notes |
|---|---|---|
| **Pipeline Coherence** | ✅ Good | 3-phase loop with clean termination logic |
| **Goal Alignment** | ✅ Good | Output structure matches temporal-input.json format |
| **Platform Agnosticism** | ⚠️ Partial | Agent MDs are now config-driven; project.yaml still hardcodes Google SecOps MCP tools |
| **Output Completeness** | ⚠️ Partial | Pass-through fields + generated fields + validation both present in Phase 4 |
| **Field Mapping Handling** | ❌ Bug | `field_mappings` format inconsistency across generator + reviewer (see 04_critical_bugs.md) |
| **Error Handling** | ✅ Good | KAPA fallback, MCP degraded mode, max-iteration FAIL path all covered |
| **Retry Logic** | ⚠️ Partial | Phase 1 delegation JSON hardcodes `iteration: 1` on all iterations |
| **Observability** | ✅ Good | Delegates to AgentWeave framework; supervisor uses `log_supervisor_action` |
| **Autonomous Execution** | ✅ Good | All agents enforce no-question, always-progress mode |

---

## Pipeline Strengths

### 1. Clean Separation of Concerns
Each agent has a single, well-defined responsibility:
- **Generator**: translation only — never validates
- **Reviewer**: validation only — never generates
- **Supervisor**: orchestration only — never translates or validates directly

### 2. Information Hiding Between Agents
Pass-through metadata fields (`workflow_id`, `app_name`, `datasource`, etc.) are correctly
isolated to Phase 4 output only. Generator and Reviewer receive only the fields they need.
This prevents context pollution and reduces token usage per invocation.

### 3. Structured Retry with Targeted Feedback
The feedback block format ensures the Generator receives specific, actionable failure messages
per validator — not a vague "failed" signal. Each `[VALIDATOR - FAILED]` block has an
`Action required:` line.

### 4. Dual Validation Report
Phase 4 output includes both `validation_report` and `platform_validation` with identical
content — accommodating consumers that expect either field name without data loss.

### 5. KAPA Integration Architecture
Generator fetches all platform knowledge dynamically via the context engine rather than
maintaining static knowledge. This makes knowledge current and reduces prompt bloat.

### 6. Platform-Agnostic Config Design (Newly Added)
`platform_config.md` correctly externalizes MCP tool names, query structure file paths,
and illegal constructs — allowing new platform onboarding without agent prompt changes.

---

## Pipeline Gaps

### 1. CRITICAL: field_mappings Format Mismatch
**Impact:** Both Generator (Step 4) and Reviewer (V2, V3) describe `field_mappings` using
the OLD dict-of-strings format `{source_field: "dest_field"}`. The actual temporal input
format is a **list of dicts**: `[{source_field, destination_field, matching_tier, ...}]`.
This means both agents will misread the field mappings on every real pipeline run.
→ See `04_critical_bugs.md` for exact fix locations.

### 2. Supervisor Hardcodes `iteration: 1` in Phase 1 Delegation
The Phase 1 delegation JSON template shows `"iteration": 1` as a literal. On retry
iterations 2 and 3, the Generator still receives `iteration: 1` and treats the run as
a first attempt, skipping Step 7 (retry feedback handling).

### 3. project.yaml Reviewer Tools Are Platform-Specific
```yaml
tools: [Read, Grep, mcp__google-secops-mcp-server__validate_dashboard_query, 
        mcp__google-secops-mcp-server__validate_rule]
```
For a new destination platform, project.yaml must be updated in addition to creating a
`platform_config.md`. True platform-agnosticism requires a dynamic tool registration
mechanism or a generic MCP validation proxy.

### 4. V4 Logtype Injection Check Contradicts Generator Behavior
Generator now treats `metadata.log_type` as a regular field_mapping entry (no first-position
injection). Reviewer V4 still checks that logtype is the FIRST condition in the events section.
This will cause V4 false failures on correctly generated queries.

### 5. Generator Output Field `analysis` Contains Stale Reference
The `analysis` example string in generator.md's Output Format still says:
`(6) logtype injected as first events: condition`
This step was removed. It will confuse LLM output quality.

---

## Output File Assessment

The pipeline produces `output.json` in temporal format. Fields present:

| Category | Fields | Status |
|---|---|---|
| Generated | `destination_query`, `generation_notes`, `reasoning`, `analysis`, `assumptions`, `warnings` | ✅ |
| Validation | `validation_report`, `platform_validation`, `platform_validation.results[]`, `validation_errors`, `data_validation` | ✅ |
| Pipeline meta | `confidence_score`, `status`, `iteration_count` | ✅ |
| Pass-through | `source_platform`, `destination_platform`, `migration_type`, `destination_metadata`, `summary`, `intent`, `use_case`, `field_mappings`, `commands`, `fields`, `functions`, `parsed_json`, `query`, `source_metadata`, `app_name`, `panel_name`, `datasource`, `usage_type`, `unmapped_fields`, `metadata`, `workflow_id`, `agentweave_sessions` | ✅ |

All temporal-input.json fields are accounted for in the output.

---

## Files Reviewed

| File | Role | Review File |
|---|---|---|
| `agents/supervisor.md` | Orchestrator | `01_supervisor_review.md` |
| `agents/generator.md` | Query Translator | `02_generator_review.md` |
| `agents/reviewer.md` | Query Validator | `03_reviewer_review.md` |
| `project.yaml` | AgentWeave config | Covered in this file + `04_critical_bugs.md` |
| `product_docs/google_secops/platform_config.md` | Platform config | Referenced throughout |
