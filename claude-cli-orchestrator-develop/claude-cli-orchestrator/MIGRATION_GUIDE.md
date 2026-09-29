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
| Observability                  | AgentWeave framework              | `execution_report.json` + `execution.log` | Automatic                                                                    |

---

## Migration Checklist

Follow these steps in order. Click each step to jump to its section.

- [ ] **[Step 1](#step-1-analyze-your-current-project)** -- Analyze Your Current Project
  - Read supervisor.md, identify which pattern you have (A, B, or C)

- [ ] **[Step 2](#step-2-choose-your-execution-pattern--view-examples)** -- Choose Your Execution Pattern & View Examples
  - Select your pattern (A = single agent, B = pipeline loop, C = step retries)
  - Copy the example workflow block for your pattern

- [ ] **[Step 3](#step-3-update-subagents-section)** -- Update `subagents:` Section
  - Fix `prompt_file` paths
  - Convert tool lists to YAML format

- [ ] **[Step 4](#step-4-write-your-workflow-block)** -- Write Your `workflow:` Block
  - Add steps, exit conditions, feedback rules based on your pattern
  - Add passthrough_fields list

- [ ] **[Step 5](#step-5-delete-supervisormd)** -- Delete `supervisor.md`
  - Remove supervisor.md file
  - Remove `supervisor:` block from project.yaml

- [ ] **[Step 6](#step-6-migrate-tools-mcpjson-optional)** -- Migrate Tools (mcp.json) [Optional]
  - If you have MCP servers, copy mcp.json to claude-cli-orchestrator/mcp/
  - Update server URLs if needed
  - Add MCP tools to subagent definitions in project.yaml

- [ ] **[Step 7](#step-7-migrate-product_docs-optional)** -- Migrate product_docs [Optional]
  - If you have platform documentation, copy to claude-cli-orchestrator/product_docs/
  - Structure: `product_docs/<platform>/` (e.g., product_docs/google_secops/)
  - Agents reference via `Read` tool in their prompts

- [ ] **[Step 8](#step-8-migrate-skills-optional)** -- Migrate Skills (Optional)
  - If you have SKILL.md files, move them to `skills/<agent_name>/`
  - Add YAML frontmatter with trigger conditions

- [ ] **[Step 9](#step-9-test--run)** -- Test & Run
  - Run: `python main.py --input path/to/input.json`
  - Verify output in `runs/<project-name>/<timestamp>/`

---

## Step 1: Analyze Your Current Project

Before you write any code, understand what your project currently does.

### What to Look For

Open your project's `agents/supervisor.md` and find:

1. **How many agents** are delegated to
   - Look for `Task tool` calls in the supervisor
   - Examples: "delegate to generator", "delegate to reviewer"

2. **Whether there is a retry loop**
   - Look for phrases like "loop until PASS", "max N iterations", "re-delegate with fixes"
   - If no loop mentioned → Pattern A (single run)

3. **What field the loop checks**
   - Look for `overall_status`, `status`, `validation_results`
   - This becomes your `exit_condition.field` in project.yaml

4. **What feedback is injected on retry**
   - Look for `feedback_for_generator`, `validation_feedback`, `issues`
   - This becomes your `feedback.field` in project.yaml

### Identifying Your Pattern

| Pattern | Description | Your Project |
|---|---|---|
| **A** | Single agent, runs once | One agent, no retry loop |
| **B** | Pipeline loop with feedback | Multiple agents, loop until reviewer passes, feedback injected to generator |
| **C** | Step-level independent retries | Multiple agents, each retries independently with its own exit condition |

**Examples:**
- **source-analyzer** = Pattern A (one `analyzer` agent)
- **query-generator** = Pattern B (generator → reviewer loop with feedback)
- **field-mapper** = Pattern C (field-extractor → field-mapper, each retries independently)

---

## Step 2: Choose Your Execution Pattern & View Examples

Once you've identified your pattern from Step 1, use the corresponding example below as a template.

### Pattern A: Single Agent, Runs Once

*Reference: **source-analyzer** project*

**When to use:** One agent, no retry loop, no exit condition. Agent runs once and produces output.

**Example workflow block:**

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

**Key points:**
- No `max_iterations` (omit it)
- No `exit_condition` (omit it)
- No `feedback` (omit it)
- Just one step with no dependencies
- Agent runs once, output is returned

---

### Pattern B: Pipeline-Level Retry Loop with Feedback

*Reference: **query-generator** project*

**When to use:** Multiple agents in sequence. Pipeline loops until a specific agent reports success (e.g., `overall_status == "PASS"`). On each retry, feedback from a reviewer is injected into a generator.

**Example workflow block:**

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

**Key points:**
- `max_iterations: 3` sets hard limit (e.g., 3 tries)
- `exit_condition` checks when to stop looping
- `feedback` rule extracts `feedback_for_generator` and injects it as `ctx_feedback`
- `fallback: generic_validation_feedback` auto-builds feedback from failed validators if field is null
- Both steps use auto-chain (no `input:` blocks)

---

### Pattern C: Independent Step-Level Retries

*Reference: **field-mapper** project*

**When to use:** Multiple agents in sequence. Each step retries independently (not a global loop). `field-mapper` only runs after `field-extractor` passes. Each step has its own `max_retries` and `exit_condition`.

**Example workflow block:**

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

**Key points:**
- No `max_iterations` (each step retries independently, not a global loop)
- Each step has its own `max_retries` and `exit_condition`
- `depends_on: [field_extractor]` means field_mapper waits for field_extractor to pass
- `input:` block on field_mapper uses template mode (selective field passing with renaming)
- `"$.field"` syntax extracts from cumulative state
- `"ctx.feedback.field_mapper"` injects step-level retry feedback

---

## Step 3: Update `subagents:` Section

Update the existing `subagents:` section in your project.yaml. Agent definitions themselves don't change, but paths and format need updating.

### Fixing `prompt_file` Paths

Claude CLI resolves `prompt_file` relative to the project root (directory containing project.yaml).

```yaml
# Before (AgentWeave)
prompt_file: analyzer.md
prompt_file: generator.md
prompt_file: agents/field_extractor.md

# After (claude-cli-orchestrator)
prompt_file: agents/analyzer.md
prompt_file: agents/generator.md
prompt_file: agents/field_extractor.md
```

**Rule:** If agents are in an `agents/` subdirectory, include the path.

### Converting Tool Lists to YAML Format

```yaml
# Before (inline list)
tools: [Read, Grep, mcp__context-engine__context_engine_agent]

# After (YAML list)
tools:
  - Read
  - Grep
  - mcp__context-engine__context_engine_agent
```

### Complete Example

```yaml
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
```

---

## Step 4: Write Your `workflow:` Block

Based on the pattern you identified in Step 1 and Step 2, add the `workflow:` block to your project.yaml.

**Copy the template from Step 2** that matches your pattern and customize these fields:

### For Pattern A (Single Agent)

1. Change `analyzer` to your agent name
2. Add all input fields to `passthrough_fields`
3. Done — no exit_condition or feedback needed

### For Pattern B (Pipeline Loop)

1. Change agent names (`generator`, `reviewer`) to match your agents
2. Update `exit_condition.step` and `exit_condition.field` to match your reviewer's output
3. Update `feedback.from.field` to the actual feedback field name in your reviewer's output
4. Update `feedback.to.step` to the generator step name
5. Add all input fields to `passthrough_fields`

### For Pattern C (Step-Level Retries)

1. Update step IDs and agent names
2. Update `exit_condition.field` for each step to match that step's output
3. Update `feedback.field` for each step
4. For steps using template mode (`input:` block):
   - Map field names using `"$.source_field"` syntax
   - Rename keys as needed: `"new_name: $.old_name"`
5. Add all input fields to `passthrough_fields`

### Key Fields Explained

| Field | Type | Required? | Example | Meaning |
|---|---|---|---|---|
| `max_iterations` | int | Pattern B only | `3` | Max pipeline loop iterations before giving up |
| `steps` | list | Yes | `[{id: generator, agent: generator}]` | Execution steps in order |
| `exit_condition` | dict | If looping | `{step: reviewer, field: overall_status, equals: PASS}` | When to stop looping |
| `feedback` | list | If looping | `[{when: {...}, from: {...}, to: {...}}]` | Feedback injection rules |
| `passthrough_fields` | list | Yes | `[source_platform, destination_platform, ...]` | Fields copied from input to output unchanged |

---

## Step 5: Delete `supervisor.md`

Once your `workflow:` block is in place, remove the supervisor agent (it's no longer needed).

### What to Delete

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

### Why?

The `WorkflowEngine` (Python code) replaces the Supervisor LLM entirely. All orchestration decisions (iteration, retry, feedback) are now config-driven in `project.yaml`. The supervisor is never invoked.

---

## Step 6: Migrate Tools (mcp.json) [Optional]

If your AgentWeave project uses MCP tools (e.g., `google-secops-mcp-server`, `context-engine`), migrate the configuration.

### What to Migrate

Your AgentWeave project has `mcp/mcp.json`. Copy it to claude-cli-orchestrator:

```
Before:
  agentweave_workspace/agentweave/projects/<project>/mcp/mcp.json

After:
  claude-cli-orchestrator/mcp/mcp.json
```

### File Structure

```
mcp/
+-- mcp.json         <- MCP server configurations
```

### mcp.json Format

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

**Server types:**
- `"type": "sse"` -- SSE (Server-Sent Events) server. Claude CLI calls GET `/sse`
- `"type": "http"` -- Streamable HTTP server. Claude CLI calls POST `/mcp`

### Adding MCP Tools to Agents

Once mcp.json is in place, add tools to your subagents in `project.yaml`:

```yaml
subagents:
  - name: reviewer
    prompt_file: agents/reviewer.md
    model: sonnet
    tools:
      - Read
      - Grep
      - mcp__google-secops-mcp-server__validate_dashboard_query
      - mcp__google-secops-mcp-server__validate_rule
      - mcp__context-engine__context_engine_agent
```

**Tool naming format:** `mcp__<server_name>__<tool_name>`

### Updating Server URLs (if needed)

If your MCP servers are at different URLs in claude-cli-orchestrator, update `mcp.json`:

```json
{
  "mcpServers": {
    "google-secops-mcp-server": {
      "type": "sse",
      "url": "http://new-host:new-port/sse"
    }
  }
}
```

### Documenting Tools in Agent Prompts

Update each agent's `.md` file to document the MCP tools it can call:

```markdown
## Available MCP Tools

- `mcp__google-secops-mcp-server__validate_dashboard_query(query)` -- live syntax validation of dashboard queries
- `mcp__google-secops-mcp-server__validate_rule(rule)` -- live syntax validation of detection rules
- `mcp__context-engine__context_engine_agent(query)` -- platform knowledge and field mapping lookups
```

### If You Don't Have MCP Tools

If your AgentWeave project doesn't use MCP tools, skip this step. You can always add them later using the [Adding a New MCP Tool](#adding-a-new-mcp-tool) section at the end of this guide.

---

## Step 7: Migrate product_docs [Optional]

If your AgentWeave project has reference documentation (e.g., platform guides, field mappings, command syntax), migrate it.

### What to Migrate

Your AgentWeave project may have product documentation files:

```
Before:
  agentweave_workspace/agentweave/projects/<project>/product_docs/
  └── google_secops/
      ├── platform_config.md
      ├── json_understanding.md
      └── query_structure/

After:
  claude-cli-orchestrator/product_docs/
  └── google_secops/
      ├── platform_config.md
      ├── json_understanding.md
      └── query_structure/
```

### Directory Structure

```
product_docs/                          <- Static reference docs (read by agents)
+-- google_secops/                     <- Platform-specific directory
|   +-- platform_config.md             <- Illegal constructs, MCP tool names
|   +-- json_understanding.md          <- UDM / Chronicle log format
|   +-- query_structure/
|       +-- secops_query_syntax.md     <- YARA-L 2.0 dashboard query syntax
|       +-- secops_rule_syntax.md      <- YARA-L 2.0 detection rule syntax
+-- splunk_to_google_secops/
    +-- command_mapping.md             <- SPL → YARA-L command translation
```

### How Agents Use product_docs

Agents reference documentation using the `Read` tool in their system prompts:

```markdown
## Reference Documentation

You have access to platform documentation via the Read tool. Key files:
- `product_docs/google_secops/platform_config.md` -- valid constructs and field names
- `product_docs/google_secops/query_structure/secops_query_syntax.md` -- YARA-L syntax rules
- `product_docs/splunk_to_google_secops/command_mapping.md` -- SPL command translation
```

Then agents call `Read` during execution:

```
Agent: "I need to look up the YARA-L syntax for events filters."
Tool call: Read("product_docs/google_secops/query_structure/secops_query_syntax.md")
```

### Organizing by Platform

Group documentation by destination platform:

```
product_docs/
+-- google_secops/              <- For destination_platform: google_secops
|   +-- platform_config.md
+-- splunk_to_google_secops/    <- For conversions from splunk
|   +-- command_mapping.md
+-- field_mapping/              <- Shared field mapping docs
    +-- splunk_fields.md
    +-- secops_fields.md
```

### Including in Agent Prompts

Reference docs in your agent's `.md` file so agents know what's available:

```markdown
## Available Reference Materials

- `product_docs/google_secops/platform_config.md` -- Field names and valid constructs
- `product_docs/splunk_to_google_secops/command_mapping.md` -- Command translation
- `product_docs/google_secops/query_structure/secops_query_syntax.md` -- Syntax reference
```

Agents will use `Read` to fetch these during execution.

### If You Don't Have product_docs

If your AgentWeave project doesn't have reference documentation, skip this step. You can create product_docs later as needed.

---

## Step 8: Migrate Skills (Optional)

If your project has SKILL.md files (supplemental context injected into agents), migrate them with trigger-based filtering.

### Directory Structure

Skills are scoped to agents. Create directories under `skills/<agent_name>/`:

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

### SKILL.md Frontmatter Format

Every SKILL.md must begin with YAML frontmatter declaring when it should be loaded:

```markdown
---
name: Google SecOps Field Mapping
description: "Field mapping rules for the generator when destination is Google SecOps."
triggers:
  destination_platform: google_secops
---

# Skill content starts here
```

### Trigger Conditions

| Condition type | Behaviour | Example |
|---|---|---|
| Single string value | Case-insensitive equality (keys and values) | `triggers: {source_platform: splunk}` loads when `payload.source_platform == "splunk"` |
| List value | Case-insensitive membership | `triggers: {destination_platform: [google_secops, chronicle]}` loads when value is in list |
| Multiple keys | ANY one matching is enough (OR logic) | Two trigger entries: skill loads if either matches |
| No `triggers` block | Always loaded for this agent | `triggers:` omitted → always inject for that agent |
| Key absent from payload | That condition is skipped | If `destination_platform` not in payload, that key is ignored; other keys still evaluated |

### Multi-Value Triggers

```markdown
---
name: Google SecOps & Chronicle Mapping
triggers:
  destination_platform:
    - google_secops
    - chronicle
---
```

### Multiple Conditions (OR Logic)

```markdown
---
name: Splunk-to-SecOps Rule Migration
triggers:
  source_platform: splunk
  destination_platform: google_secops
  migration_type: rule
---
```

The skill loads when **any one** of the three conditions matches (OR logic).
If you need a skill that is truly platform-specific (e.g. only for Splunk→SecOps), use a
single unambiguous trigger key rather than multiple keys:

```markdown
---
name: Splunk Source Skills
triggers:
  source_platform: splunk
---
```

### Step-by-Step: Migrate One Skill

**Before** (AgentWeave `.claude/skills/splunk/SKILL.md`):
```markdown
---
name: splunk
description: "Splunk SPL extraction skills. TRIGGER when: source_platform is 'splunk'.
              DO NOT TRIGGER when: source_platform is any other value."
---

# Source Analyzer -- Splunk Skills
...
```

**After** (`skills/analyzer/splunk/SKILL.md`):
```markdown
---
name: Splunk Extraction
description: "Splunk SPL extraction skills for the analyzer agent."
triggers:
  source_platform: splunk
---

# Source Analyzer -- Splunk Skills
...
```

**Changes:**
1. **Move** from `.claude/skills/splunk/SKILL.md` → `skills/analyzer/splunk/SKILL.md`
2. **Add** structured `triggers:` block (replaces free-text description)
3. **Remove** "DO NOT TRIGGER" language (now enforced by engine)

---

## Step 9: Test & Run

Now you're ready to test your migrated project.

### Running for the First Time

```bash
cd claude-cli-orchestrator
python main.py --input path/to/input.json
```

Or validate without calling Claude:

```bash
python main.py --input path/to/input.json --dry-run
```

### Where Output Goes

All artifacts go to `runs/<project-name>/<UTC-timestamp>/`:

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

### Reading the Output

- **output.json** -- Final result (passthrough_fields + step outputs + status)
- **execution.log** -- Live [RUN] log lines (what printed to console)
- **execution_report.json** -- Full observability (cost, tokens, per-step metrics)

### If Something Goes Wrong

Consult [Troubleshooting](#troubleshooting) section below.

---

## Reference Material

### Auto-Chain vs Template Mode

The orchestrator supports two ways to wire input into a step. Choose based on your needs.

#### Auto-Chain (Recommended for most steps)

**When to use:** You want every step to receive all available fields (input + all prior step outputs).

**What it looks like:**

```yaml
steps:
  - id: generator
    agent: generator
    # No input: block -> auto-chain mode
```

**What the agent receives:**

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
  # (on iteration 2+)
  overall_status: "FAIL",
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

**When to use:** You want to pass only specific fields, rename them, or construct dynamic payloads.

**What it looks like:**

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

**What the agent receives:**

```json
{
  "source_platform_name": "splunk",
  "destination_platform_name": "google_secops",
  "input_fields": ["src_ip", "EventCode", ...],
  "iteration": 1,
  "fixes_required": null
}
```

Only those four fields — nothing else.

**Template expression reference:**

| Expression | Resolves to | Example |
|---|---|---|
| `"$.field"` | Root payload field | `"$.source_platform"` → `payload["source_platform"]` |
| `"steps.STEP_ID.field"` | Output field from a completed step | `"steps.field_extractor.extracted_fields"` |
| `"steps.STEP_ID.a.b"` | Nested dot-path into step output | `"steps.reviewer.validation_results.0.is_valid"` |
| `"ctx.iteration"` | Current iteration / attempt number | `"ctx.iteration"` → `1` on first try, `2` on retry |
| `"ctx.feedback.STEP_ID"` | Step-level feedback for that step | `"ctx.feedback.field_extractor"` → feedback text from field_extractor's last retry |
| `"literal string"` | Passed through as-is | `"hello"` → `"hello"` |
| `42` / `true` | Literal int / bool | `42` → `42` |

---

### supervisor.md → project.yaml Mapping Table

Use this table to translate supervisor.md patterns into project.yaml configuration.

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

Use this as a starting point for your project.yaml:

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

Once migration is complete, you may need to add new MCP tools to agents.

### Step 1 -- Register the server in `mcp/mcp.json`

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

Use `"type": "sse"` for SSE servers (GET to `/sse`).  
Use `"type": "http"` for Streamable HTTP servers (POST to `/mcp`).

### Step 2 -- Add the tool name to the agent in `project.yaml`

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

### Step 3 -- Document the tool in the agent's `.md` prompt

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
- **Bug/Issue:** Report at https://github.com/anthropics/claude-code/issues
