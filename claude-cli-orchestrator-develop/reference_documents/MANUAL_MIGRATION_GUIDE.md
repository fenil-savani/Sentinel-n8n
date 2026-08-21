# AgentWeave → claude-cli-orchestrator Migration Guide

Convert an existing AgentWeave project to run under the `WorkflowEngine` in `claude-cli-orchestrator`
(folder: `claude-cli-orchestrator/` -- project name: `claude-cli-orchestrator`).

The three projects -- **source-analyzer**, **field-mapper**, and **query-generator** --
are the reference examples throughout this guide.

---

## What Changes, What Doesn't

| Component                      | AgentWeave                        | claude-cli-orchestrator                                       | Action required                                                              |
|--------------------------------|-----------------------------------|---------------------------------------------------------------|------------------------------------------------------------------------------|
| Agent `.md` files              | Unchanged                         | Unchanged                                                     | **None** -- reused as-is                                                     |
| `mcp.json`                     | Unchanged                         | Unchanged                                                     | **None** -- reused as-is                                                     |
| `subagents:` in `project.yaml` | Unchanged                         | Same format                                                   | Minor: update `prompt_file` paths                                            |
| `supervisor.md`                | LLM agent with `Task` tool        | Not used -- Python handles it                                 | **Delete** the file after migration, Add `workflow:` block to `project.yaml` |
| Iteration loop                 | Supervisor LLM decides            | Config-driven in YAML                                         | Add `workflow:` block to `project.yaml`                                      |
| Input wiring                   | Supervisor explicitly maps fields | Auto-chain (cumulative state)                                 | Remove explicit field mapping -- no `input:` block needed                    |
| Observability                  | AgentWeave framework              | `execution_report.json` + `execution.log`                    | Automatic                                                                    |

---

## Migration Checklist

- [ ] **[Step 1](#step-1-identify-your-pattern--copy-the-workflow-template)** -- Identify Your Pattern & Copy the Workflow Template
- [ ] **[Step 2](#step-2-update-projectyaml)** -- Update `project.yaml`
- [ ] **[Step 3](#step-3-migrate-optional-assets)** -- Migrate Optional Assets (mcp.json, product_docs, skills)
- [ ] **[Step 4](#step-4-test--run)** -- Test & Run

---

## Step 1: Identify Your Pattern & Copy the Workflow Template

Open `agents/supervisor.md` and answer these four questions. Your answers determine which pattern to use and pre-fill the workflow block you'll paste in Step 2.

**What to look for:**

1. **How many agents** are delegated to -- look for `Task tool` calls ("delegate to generator", "delegate to reviewer")
2. **Whether there is a retry loop** -- look for "loop until PASS", "max N iterations", "re-delegate with fixes". If none → Pattern A.
3. **What field the loop checks** -- look for `overall_status`, `status`, `validation_results`. This becomes `exit_condition.field`.
4. **What feedback is injected on retry** -- look for `feedback_for_generator`, `validation_feedback`, `issues`. This becomes `feedback.field`.

**Identifying your pattern:**

| Pattern | Description | Example project |
|---|---|---|
| **A** | Single agent, runs once | **source-analyzer** (one `analyzer` agent) |
| **B** | Pipeline-level retry loop with feedback | **query-generator** (generator → reviewer loop) |
| **C** | Step-level independent retries | **field-mapper** (field-extractor → field-mapper, each retries independently) |

---

### Pattern A: Single Agent, Runs Once

**When to use:** One agent, no retry loop, no exit condition. Agent runs once and produces output.

```yaml
# project.yaml
workflow:
  steps:
    - id: analyzer
      agent: analyzer
      # No input: block -> auto-chain mode
      # analyzer receives: all input.json fields + ctx_iteration + ctx_feedback

  passthrough_fields:
    - workflow_id
    - app_name
    - panel_name
    - destination_platform
    - migration_type
    - destination_metadata
    - agentweave_sessions
```

**Customize:** Change `analyzer` to your agent name. Update `passthrough_fields` to all input fields that should appear unchanged in output. No `max_iterations`, `exit_condition`, or `feedback` needed.

---

### Pattern B: Pipeline-Level Retry Loop with Feedback

**When to use:** Multiple agents in sequence. Pipeline loops until a specific agent reports success (e.g., `overall_status == "PASS"`). On each retry, feedback from a reviewer is injected into a generator.

```yaml
# project.yaml
workflow:
  max_iterations: 3

  steps:
    - id: generator
      agent: generator
      # Auto-chain: receives all input fields + ctx_iteration + ctx_feedback
      # On retry: also sees reviewer's overall_status, validation_results,
      #           and feedback_for_generator from the previous iteration

    - id: reviewer
      agent: reviewer
      depends_on: [generator]
      # Auto-chain: receives all input fields + generator's {result, analysis,
      #             reasoning, assumptions, warnings} automatically

  exit_condition:
    step: reviewer
    field: overall_status
    equals: "PASS"

  feedback:
    - when:
        step: reviewer
        field: overall_status
        not_equals: "PASS"
      from:
        step: reviewer
        field: feedback_for_generator     # field in reviewer's JSON output
      to:
        step: generator
        as: ctx_feedback                  # available as ctx_feedback on next iteration
      fallback: generic_validation_feedback  # built-in: parses validation_results[]

  passthrough_fields:
    - source_platform
    - destination_platform
    - migration_type
    - destination_metadata
    - summary
    - intent
    - use_case
    - field_mappings
    - commands
    - fields
    - functions
    - parsed_json
    - source_metadata
    - app_name
    - panel_name
    - datasource
    - usage_type
    - unmapped_fields
    - metadata
    - workflow_id
    - agentweave_sessions
```

**Customize:**
1. Change agent names (`generator`, `reviewer`) to match your agents
2. Update `exit_condition.step` and `exit_condition.field` to match your reviewer's output field
3. Update `feedback.from.field` to the actual feedback field name in your reviewer's output
4. Update `feedback.to.step` to the generator step name
5. Update `passthrough_fields` to all input fields that should pass through unchanged

**Key points:**
- `max_iterations: 3` sets the hard limit
- `fallback: generic_validation_feedback` auto-builds feedback from failed validators if the field is null
- Both steps use auto-chain (no `input:` blocks)

---

### Pattern C: Independent Step-Level Retries

**When to use:** Multiple agents in sequence. Each step retries independently (not a global loop). Each step has its own `max_retries` and `exit_condition`.

```yaml
# project.yaml
workflow:
  steps:
    # Step 1: Extract fields from source and destination platform parser files
    # Retries up to 3 times if CSVs are missing or invalid
    - id: field_extractor
      agent: field-extractor
      max_retries: 3
      exit_condition:
        field: status
        equals: "PASS"
      feedback:
        field: issues      # field_extractor output field containing error list

    # Step 2: Map fields using 5-tier matching strategy
    # Runs only after field_extractor passes; retries up to 3 times
    - id: field_mapper
      agent: field-mapper
      depends_on: [field_extractor]
      max_retries: 3
      exit_condition:
        field: overall_status
        equals: "PASS"
      feedback:
        field: issues      # field_mapper output field containing mapping issues
      input:
        source_platform_name:      "$.source_platform"
        destination_platform_name: "$.destination_platform"
        source_fields_to_map:      "$.fields"
        migration_type:            "$.migration_type"
        parsed_json:               "$.parsed_json"
        iteration:                 "ctx.iteration"
        fixes_required:            "ctx.feedback.field_mapper"

  passthrough_fields:
    - source_platform
    - destination_platform
    - migration_type
    - destination_metadata
    - app_name
    - fields
    - parsed_json
    - workflow_id
    - agentweave_sessions
```

**Customize:**
1. Update step IDs and agent names
2. Update `exit_condition.field` for each step to match that step's actual output key
3. Update `feedback.field` for each step to the field containing error/issue details
4. For steps using template mode (`input:` block): map field names using `"$.source_field"` syntax and rename keys as needed
5. Update `passthrough_fields` to all input fields that should pass through unchanged

**Key points:**
- No `max_iterations` -- each step retries independently, not as a global loop
- `depends_on: [field_extractor]` means field_mapper waits for field_extractor to pass
- The `input:` block on field_mapper uses template mode (selective field passing with renaming)
- `"ctx.feedback.field_mapper"` injects step-level retry feedback

---

## Step 2: Update `project.yaml`

With your workflow block from Step 1 ready, make three edits to your project's `project.yaml`.

### 2a. Fix `subagents:` paths and tool format

Claude CLI resolves `prompt_file` relative to the project root. Add the `agents/` prefix if not already present, and convert inline tool lists to YAML list format:

```yaml
# Before (AgentWeave)
subagents:
  - name: generator
    prompt_file: generator.md       # missing agents/ prefix
    tools: [Read, Grep, mcp__context-engine__context_engine_agent]  # inline list

# After (claude-cli-orchestrator)
subagents:
  - name: generator
    prompt_file: agents/generator.md
    description: "Generates YARA-L 2.0 from SPL input."
    model: sonnet                   # haiku | sonnet | opus
    tools:
      - Read
      - Grep
      - mcp__context-engine__context_engine_agent

  - name: reviewer
    prompt_file: agents/reviewer.md
    description: "Validates generated output via 5 validators."
    model: sonnet
    tools:
      - Read
      - Grep
      - mcp__google-secops-mcp-server__validate_dashboard_query
      - mcp__google-secops-mcp-server__validate_rule
```

### 2b. Add the `workflow:` block

Paste the workflow block you copied from Step 1 after the `subagents:` section.

**Key fields reference:**

| Field | Type | Required? | Meaning |
|---|---|---|---|
| `max_iterations` | int | Pattern B only | Max pipeline loop iterations before giving up |
| `steps` | list | Yes | Execution steps in order |
| `exit_condition` | dict | If looping | When to stop looping |
| `feedback` | list | If looping | Feedback injection rules |
| `passthrough_fields` | list | Yes | Fields copied from input to output unchanged |

### 2c. Delete `supervisor.md` and its project.yaml block

The `WorkflowEngine` (Python code) replaces the Supervisor LLM entirely. All orchestration decisions (iteration, retry, feedback) are now config-driven in `project.yaml`. The supervisor is never invoked.

1. **Delete the file:** `agents/supervisor.md`
2. **Remove from project.yaml:**
   ```yaml
   # DELETE THIS ENTIRE SECTION:
   supervisor:
     prompt_file: agents/supervisor.md
     description: "..."
     model: sonnet
     tools: [...]
   ```

---

## Step 3: Migrate Optional Assets

Skip any sub-section that doesn't apply to your project.

### 3a. mcp.json (if you use MCP tools)

Copy your MCP config into the orchestrator's `mcp/` directory:

```
Before:  agentweave_workspace/agentweave/projects/<project>/mcp/mcp.json
After:   claude-cli-orchestrator/mcp/mcp.json
```

**mcp.json format:**
```json
{
  "mcpServers": {
    "google-secops-mcp-server": {
      "type": "sse",
      "url": "http://10.50.12.43:8000/sse"
    },
    "context-engine": {
      "type": "http",
      "url": "http://10.5.50.275:8090/mcp"
    }
  }
}
```

- `"type": "sse"` -- SSE server, Claude CLI calls GET `/sse`
- `"type": "http"` -- Streamable HTTP server, Claude CLI calls POST `/mcp`

Then add the MCP tools to your `subagents:` in project.yaml using the format `mcp__<server_name>__<tool_name>`:

```yaml
subagents:
  - name: reviewer
    tools:
      - Read
      - Grep
      - mcp__google-secops-mcp-server__validate_dashboard_query
      - mcp__google-secops-mcp-server__validate_rule
      - mcp__context-engine__context_engine_agent
```

Also update each agent's `.md` file to document the tools it can call:

```markdown
## Available MCP Tools

- `mcp__google-secops-mcp-server__validate_dashboard_query(query)` -- live syntax validation of dashboard queries
- `mcp__google-secops-mcp-server__validate_rule(rule)` -- live syntax validation of detection rules
- `mcp__context-engine__context_engine_agent(query)` -- platform knowledge and field mapping lookups
```

If server URLs changed, update them directly in `mcp.json` before running.

---

### 3b. product_docs (if you have reference documentation)

Copy platform documentation into the orchestrator, keeping the same directory structure:

```
Before:  agentweave_workspace/agentweave/projects/<project>/product_docs/
After:   claude-cli-orchestrator/product_docs/

product_docs/
+-- google_secops/                     <- Platform-specific directory
|   +-- platform_config.md             <- Illegal constructs, MCP tool names
|   +-- json_understanding.md          <- UDM / Chronicle log format
|   +-- query_structure/
|       +-- secops_query_syntax.md     <- YARA-L 2.0 dashboard query syntax
|       +-- secops_rule_syntax.md      <- YARA-L 2.0 detection rule syntax
+-- splunk_to_google_secops/
    +-- command_mapping.md             <- SPL → YARA-L command translation
```

Agents reference these files via the `Read` tool. Update each agent's `.md` to list available docs:

```markdown
## Reference Documentation

You have access to platform documentation via the Read tool. Key files:
- `product_docs/google_secops/platform_config.md` -- valid constructs and field names
- `product_docs/google_secops/query_structure/secops_query_syntax.md` -- YARA-L syntax rules
- `product_docs/splunk_to_google_secops/command_mapping.md` -- SPL command translation
```

Group docs by destination platform (`google_secops/`, `splunk_to_google_secops/`, `field_mapping/`, etc.).

---

### 3c. Skills (if you have SKILL.md files)

Skills are injected into agents based on trigger conditions. Each skill must be scoped to an agent.

**Directory structure:**
```
skills/
+-- analyzer/
|   +-- splunk/SKILL.md          <- triggers: {source_platform: splunk}
|   +-- dynatrace/SKILL.md       <- triggers: {source_platform: dynatrace}
+-- generator/
|   +-- google_secops/SKILL.md   <- triggers: {destination_platform: google_secops}
+-- reviewer/
    +-- validation/SKILL.md      <- no triggers -- always loaded for reviewer
```

**Important:** If `skills/<agent_name>/` does not exist, nothing is injected. There is no global fallback.

**Add YAML frontmatter** to every SKILL.md declaring when it should load:

```markdown
---
name: Google SecOps Field Mapping
description: "Field mapping rules for the generator when destination is Google SecOps."
triggers:
  destination_platform: google_secops
---

# Skill content starts here
```

**Trigger rules:**

| Condition type | Behaviour | Example |
|---|---|---|
| Single string value | Case-insensitive equality | `triggers: {source_platform: splunk}` loads when `payload.source_platform == "splunk"` |
| List value | Case-insensitive membership | `triggers: {destination_platform: [google_secops, chronicle]}` |
| Multiple keys | ANY one matching is enough (OR logic) | Skill loads if any key matches |
| No `triggers` block | Always loaded for this agent | Omit `triggers:` entirely |
| Key absent from payload | That condition is skipped | Other keys still evaluated |

**Migrating a skill from AgentWeave:**

```markdown
# Before: .claude/skills/splunk/SKILL.md
---
name: splunk
description: "Splunk SPL extraction skills. TRIGGER when: source_platform is 'splunk'.
              DO NOT TRIGGER when: source_platform is any other value."
---

# After: skills/analyzer/splunk/SKILL.md
---
name: Splunk Extraction
description: "Splunk SPL extraction skills for the analyzer agent."
triggers:
  source_platform: splunk
---
```

Three changes: (1) move to `skills/<agent_name>/...`, (2) add structured `triggers:` block, (3) remove "DO NOT TRIGGER" language (now enforced by engine).

---

## Step 4: Test & Run

```bash
cd claude-cli-orchestrator

# Validate without calling Claude (dry run):
python main.py --input path/to/input.json --dry-run

# Full run:
python main.py --input path/to/input.json
```

**Where output goes** -- all artifacts land in `runs/<project-name>/<UTC-timestamp>/`:

```
runs/query-generator/20260420T074100Z/
+-- input.json               <- copy of your input
+-- generator_iter1.json     <- step 1, iteration 1 (payload + result)
+-- reviewer_iter1.json      <- step 2, iteration 1
+-- generator_iter2.json     <- step 1, iteration 2 (if retry)
+-- output.json              <- final assembled output
+-- execution_report.json    <- cost, tokens, turns, trace (machine-readable)
+-- execution.log            <- human-readable [RUN] timestamped log
```

**Reading the output:**
- **output.json** -- Final result (passthrough_fields + step outputs + status)
- **execution.log** -- Live [RUN] log lines (what printed to console)
- **execution_report.json** -- Full observability (cost, tokens, per-step metrics)

---

## Reference Material

### Auto-Chain vs Template Mode

The orchestrator supports two ways to wire input into a step.

#### Auto-Chain (Recommended for most steps)

No `input:` block -- every step receives all available fields (input + all prior step outputs).

```yaml
steps:
  - id: generator
    agent: generator
    # No input: block -> auto-chain mode
```

The agent receives the full cumulative state:

```
cumulative_state = {
  # Original input fields
  source_platform: "splunk",
  destination_platform: "google_secops",
  summary: "...",
  field_mappings: [...],

  # Auto-injected context
  ctx_iteration: 1,
  ctx_feedback: null,

  # Prior step outputs (added after each step runs)
  overall_status: "FAIL",          # (on iteration 2+)
  validation_results: [...],
  feedback_for_generator: "..."
}
```

**State evolution for query-generator:**

```
Input → cumulative_state = {source_platform, destination_platform, ...}
         generator runs
         ↓ generator output merged in
         cumulative_state += {result, analysis, reasoning, assumptions, warnings}
         reviewer runs
         ↓ reviewer output merged in
         cumulative_state += {overall_status, validation_results, feedback_for_generator}

Iteration 2:
         generator runs again
         → sees ALL of the above + ctx_iteration: 2, ctx_feedback: "..."
```

**Two context keys injected automatically:**

| Key | Type | Value |
|---|---|---|
| `ctx_iteration` | int | Current pipeline iteration (1-based) |
| `ctx_feedback` | str \| null | Feedback text set by `feedback:` rule (if applicable) |

#### Template Mode (For selective field passing)

Add an `input:` block to pass only specific fields, rename them, or construct dynamic payloads:

```yaml
steps:
  - id: field_extractor
    agent: field-extractor
    input:
      source_platform_name:      "$.source_platform"    # rename key
      destination_platform_name: "$.destination_platform"
      input_fields:              "$.fields"              # rename key
      iteration:                 "ctx.iteration"         # context key
      fixes_required:            "ctx.feedback.field_extractor"  # step feedback
```

The agent receives only those keys -- nothing else.

**Template expression reference:**

| Expression | Resolves to | Example |
|---|---|---|
| `"$.field"` | Root payload field | `"$.source_platform"` → `payload["source_platform"]` |
| `"steps.STEP_ID.field"` | Output field from a completed step | `"steps.field_extractor.extracted_fields"` |
| `"steps.STEP_ID.a.b"` | Nested dot-path into step output | `"steps.reviewer.validation_results.0.is_valid"` |
| `"ctx.iteration"` | Current iteration / attempt number | `1` on first try, `2` on retry |
| `"ctx.feedback.STEP_ID"` | Step-level feedback for that step | feedback text from field_extractor's last retry |
| `"literal string"` | Passed through as-is | `"hello"` → `"hello"` |
| `42` / `true` | Literal int / bool | `42` → `42` |

---

### supervisor.md → project.yaml Mapping Table

| supervisor.md pattern | project.yaml equivalent |
|---|---|
| `Phase N: Delegate to <agent>` | One entry in `steps:` with `agent: <name>` |
| `Await the agent's response` | `depends_on: [previous_step]` |
| `if status == PASS: proceed / exit` | `exit_condition: {field: status, equals: "PASS"}` |
| `if FAIL: re-delegate with fixes (pipeline)` | `max_iterations: N` + `feedback:` rule |
| `if FAIL: retry same step` | `max_retries: N` + `exit_condition` on that step |
| `loop until PASS, max N iterations` | `max_iterations: N` + pipeline `exit_condition` |
| `inject feedback_for_generator on retry` | `feedback: {from: reviewer.feedback_for_generator, as: ctx_feedback}` |
| `pass-through fields to output.json` | `passthrough_fields:` list |
| `"Fields to Pass to <agent>"` table | Auto-chain (all fields pass automatically) |
| `"Fields to Pass Through Unchanged"` | `passthrough_fields:` list |

---

### Full project.yaml Template

```yaml
# =============================================================================
# <Project Name> -- claude-cli-orchestrator Configuration
# =============================================================================
name: my-project
display_name: "My Project"
description: "What this project does."

# No supervisor: block -- orchestration is handled by WorkflowEngine in code.

subagents:
  - name: generator
    prompt_file: agents/generator.md
    description: "Generates YARA-L 2.0 from SPL input."
    model: sonnet                # haiku | sonnet | opus
    tools:
      - Read
      - Grep
      - mcp__context-engine__context_engine_agent

  - name: reviewer
    prompt_file: agents/reviewer.md
    description: "Validates generated output via 5 validators."
    model: sonnet
    tools:
      - Read
      - Grep
      - mcp__google-secops-mcp-server__validate_dashboard_query
      - mcp__google-secops-mcp-server__validate_rule

workflow:
  max_iterations: 3        # omit for Pattern A (no loop) or Pattern C (step-level retries)

  steps:
    - id: generator
      agent: generator
      # No input: block -> auto-chain (receives full cumulative_state)

    - id: reviewer
      agent: reviewer
      depends_on: [generator]
      # No input: block -> auto-chain (generator's output included automatically)

  # Pipeline exits when reviewer.overall_status == "PASS"
  exit_condition:
    step: reviewer
    field: overall_status
    equals: "PASS"

  # When reviewer fails, inject its feedback into the next generator call
  feedback:
    - when:
        step: reviewer
        field: overall_status
        not_equals: "PASS"
      from:
        step: reviewer
        field: feedback_for_generator
      to:
        step: generator
        as: ctx_feedback
      fallback: generic_validation_feedback   # built-in: parses validation_results[]

  passthrough_fields:
    - source_platform
    - destination_platform
    - migration_type
    - workflow_id
    # add all fields that should be copied from input to output unchanged
```

---

## What Runs After Migration

When you run `python main.py --input samples/input.json`, here's what happens:

### Files Read

```
project.yaml              ← loaded by WorkflowEngine
agents/generator.md       ← loaded by AgentRunner (base prompt, cached)
agents/reviewer.md        ← loaded by AgentRunner (base prompt, cached)
mcp/mcp.json              ← passed to every Claude CLI subprocess
skills/<agent_name>/**    ← trigger-filtered SKILL.md injected per agent per call
```

### Files Written

```
runs/<name>/<ts>/input.json              ← copy of input
runs/<name>/<ts>/generator_iter1.json    ← payload sent + result received
runs/<name>/<ts>/reviewer_iter1.json
runs/<name>/<ts>/generator_iter2.json    ← if retry
runs/<name>/<ts>/output.json             ← final assembled output
runs/<name>/<ts>/execution_report.json   ← cost, tokens, turns, trace (machine-readable)
runs/<name>/<ts>/execution.log           ← human-readable [RUN] timestamped log
logs/<ts>.log                            ← Python root logger output
```

### Console Output (Live)

```
[RUN] ==============================================================
[RUN] >> WORKFLOW START  |  query-generator  |  claude-sonnet-4-6
[RUN]   run_id  : 20260420T074100Z
[RUN]
[RUN] ---- Iteration 1 / 3 ------------------------------------
[RUN]
[RUN] >> [generator] STARTING
[RUN]   mode    : auto-chain  |  payload: 23 keys
[RUN]   tools   : Read, Grep, mcp__context-engine__context_engine_agent
[RUN]   input   : {"source_platform": "splunk", ...}
[RUN]
[RUN] [OK] [generator] COMPLETE  turns=8  cost=$0.1281  in=58  out=7467  cache_r=255860  elapsed=193.5s
[RUN]   output_keys : [result, analysis, reasoning, assumptions, warnings]
[RUN]
[RUN] >> [reviewer] STARTING
[RUN]   mode    : auto-chain  |  payload: 28 keys
[RUN]   tools   : Read, Grep, mcp__google-secops-mcp-server__validate_rule
[RUN]
[RUN] [OK] [reviewer] COMPLETE  turns=6  cost=$0.1580  in=44  out=2100  cache_r=90000  elapsed=108.4s
[RUN]   output_keys : [overall_status, validation_results, feedback_for_generator]
[RUN]
[RUN] [X] EXIT CONDITION  step=reviewer  field=overall_status  "FAIL" != "PASS"  -> RETRY
[RUN] ** FEEDBACK -> [generator]  source=[reviewer]  1050 chars
[RUN]
[RUN] ---- Iteration 2 / 3 ------------------------------------
[RUN] >> [generator] STARTING
[RUN] [OK] [generator] COMPLETE  turns=8  cost=$0.1281  ...
[RUN] >> [reviewer] STARTING
[RUN] [OK] [reviewer] COMPLETE  turns=6  cost=$0.1580  ...
[RUN]
[RUN] [OK] EXIT CONDITION  step=reviewer  field=overall_status  "PASS" == "PASS"  -> PASS
[RUN]
[RUN] [OK] WORKFLOW COMPLETE  |  PASS [OK]  |  iterations=2  cost=$0.3454  elapsed=375.9s
[RUN]   tokens  : in=22430  out=3900  turns=14
[RUN] ==============================================================
```

### Summary Table (At End)

```
==========================================================================================
  Workflow:   query-generator          Status: PASS [OK]
  Model:      claude-sonnet-4-6
  Report:     runs/query-generator/20260420T074100Z/execution_report.json
==========================================================================================

  Execution Trace
  --------------------------------------------------------------------------------------
  iter  step                 turns       cost   in_tok  out_tok  cache_r   elapsed  ok
     1  generator                8    $0.0593       58     7467   255860    193.5s  [OK]
     2  generator                8    $0.1281       58     7467   255860    193.5s  [OK]
     2  reviewer                 6    $0.1580       44     2100    90000    108.4s  [OK]
  --------------------------------------------------------------------------------------
        TOTAL                   22    $0.3454    22430     3900   511720    375.9s
==========================================================================================
```

---

## Adding a New MCP Tool

### 1. Register the server in `mcp/mcp.json`

```json
{
  "mcpServers": {
    "google-secops-mcp-server": {
      "type": "sse",
      "url": "http://10.50.12.43:8000/sse"
    },
    "context-engine": {
      "type": "http",
      "url": "http://10.5.50.275:8090/mcp"
    }
  }
}
```

Use `"type": "sse"` for SSE servers (GET to `/sse`). Use `"type": "http"` for Streamable HTTP servers (POST to `/mcp`).

### 2. Add the tool name to the agent in `project.yaml`

```yaml
subagents:
  - name: reviewer
    tools:
      - Read
      - Grep
      - mcp__google-secops-mcp-server__validate_dashboard_query
      - mcp__google-secops-mcp-server__validate_rule
      - mcp__context-engine__context_engine_agent
```

### 3. Document the tool in the agent's `.md` prompt

```markdown
## Available MCP Tools

- `mcp__google-secops-mcp-server__validate_dashboard_query(query)` -- call this for V5 live syntax validation of dashboard queries.
- `mcp__google-secops-mcp-server__validate_rule(rule)` -- call this for V5 live syntax validation of detection rules.
- `mcp__context-engine__context_engine_agent(query)` -- call this for platform knowledge and field documentation lookups.
```

No Python changes required.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `project.yaml not found` | Wrong working directory | `cd claude-cli-orchestrator` before running |
| `agent 'X' not in subagents` | Step references unknown agent | Check `agent:` in step matches a `name:` in `subagents:` |
| `prompt file not found` | Wrong `prompt_file` path | Use `agents/filename.md` (relative to project root) |
| Step always returns FAIL | Wrong exit condition field name | Check agent's JSON output keys vs `exit_condition.field` |
| Feedback not injected | `feedback_for_generator` field null | Add `fallback: generic_validation_feedback` to feedback rule |
| Skills not loading | `skills/<agent>/` directory missing | Create it -- no global fallback by design |
| Skill loads when it shouldn't | Trigger too broad | Narrow the `triggers:` block -- any single matching key loads the skill (OR logic) |
| `cost_usd` always 0.0 | Old Claude CLI version | Ensure CLI returns `total_cost_usd` in JSON envelope |
| Fields missing in second iteration | Using template mode instead of auto-chain | Remove `input:` block to let cumulative state flow |
| `UnicodeDecodeError on Windows` | Encoding issue | Already fixed in cli_client.py (uses `encoding="utf-8"`) |
| `NoneType has no attribute strip` | Claude CLI returned no output | Check `--verbose` for stderr; MCP server may have crashed |
| `JsonExtractionError: no JSON found` | Agent returned plain text instead of JSON | Check agent's Output Schema in `.md` file |
| `ValueError: Step 'X' depends_on unknown step 'Y'` | Step ID typo in depends_on | Check spelling in `project.yaml` |

---

## Getting Help

- **Read:** `README.md` for full architecture and reference
- **Question:** Check this guide's sections (use Ctrl+F to search)
