# claude-cli-orchestrator

A **zero-SDK, project-agnostic** multi-agent orchestration engine powered by **Claude CLI**.

Define agents and workflows in YAML. Run any multi-agent pipeline without writing Python per project.

> **Project name:** `claude-cli-orchestrator`  
> **Folder:** `claude-cli-orchestrator/`

---

## What Problem This Solves

AgentWeave uses the Claude Agent SDK. This project uses the Claude CLI binary as a subprocess.
Use this when you want to run an AgentWeave-style multi-agent pipeline via Claude CLI -- without
an API key, with native file tools (`Read`, `Grep`, `Bash`) and MCP tool support built-in.

This engine replaces the Supervisor LLM with a deterministic Python `WorkflowEngine` that:

- Reads a declarative `project.yaml` to discover agents, execution order, and retry rules
- Invokes each agent via Claude CLI subprocess
- Handles feedback injection, retry loops, and output assembly
- Writes full observability artifacts per run -- **zero LLM cost for orchestration decisions**

| | Claude CLI | Anthropic Python SDK |
|---|---|---|
| Auth | Claude account (browser login) | API key |
| Cost model | Subscription | Per-token billing |

---

## High-Level Architecture

```
┌---------------------------------------------------------------------┐
|                      claude-cli-orchestrator/                        |
|                                                                      |
|  ┌-------------┐   reads    ┌---------------┐                       |
|  | project.yaml|----------▶| WorkflowEngine | (core/orchestrator.py)|
|  | (workflow   |            |               |                       |
|  |  definition)|            | - loads DAG   |                       |
|  +-------------┘            | - loop control|                       |
|                              | - RunLogger   |                       |
|  ┌-------------┐            +------┬--------┘                       |
|  | agents/*.md  |                  | runs each step                  |
|  | (system      |◀-- loads --------▼                                 |
|  |  prompts)    |          ┌---------------┐                        |
|  +-------------┘          | TaskDispatcher | (core/task_dispatcher) |
|                             |               |                        |
|  ┌-------------┐           | - cumul state |                        |
|  |skills/<agent>|◀-inject--| - feedback    |                        |
|  |  /SKILL.md   |  filtered| - retry logic |                        |
|  | (trigger-    |  by      | - tracing     |                        |
|  |  matched)    |  payload +------┬--------┘                        |
|  +-------------┘                 | invokes                          |
|                                   ▼                                  |
|  ┌-------------┐         ┌---------------┐                         |
|  | mcp/mcp.json|◀------  |  AgentRunner  | (core/agent_runner.py)  |
|  | (MCP server |  config |               |                         |
|  |  endpoints) |         | builds prompt |                         |
|  +-------------┘         | calls claude  |                         |
|                            | parses JSON  |                         |
|  ┌-------------┐           +------┬-------┘                         |
|  | product_docs|◀-- Read --       | subprocess                      |
|  | (platform   |                  ▼                                  |
|  |  knowledge) |         ┌---------------┐                         |
|  +-------------┘         |  Claude CLI   |  (external binary)       |
|                            |  binary      |                         |
|  ┌-------------┐           +------┬-------┘                         |
|  | runs/<name>/|◀- writes --------┘                                  |
|  | <timestamp>/|                                                     |
|  | (artifacts) |                                                     |
|  +-------------┘                                                     |
+---------------------------------------------------------------------┘
```

### Component Responsibilities

| Component | File | Responsibility |
|---|---|---|
| **WorkflowEngine** | `core/orchestrator.py` | Loads `project.yaml`, creates run dir, instantiates RunLogger, drives pipeline, assembles output |
| **TaskDispatcher** | `core/task_dispatcher.py` | Executes steps in topological order, manages cumulative state, resolves template expressions, handles retry and feedback injection |
| **AgentRunner** | `core/agent_runner.py` | Builds system prompt (base + trigger-filtered skills), serializes payload, calls Claude CLI, parses JSON output |
| **ClaudeCliClient** | `core/cli_client.py` | Low-level subprocess wrapper -- builds CLI command, passes payload via stdin, parses JSON envelope |
| **RunLogger** | `core/run_logger.py` | Per-run structured logger -- writes `[RUN]` console lines, `execution.log`, and `execution.jsonl` |
| **WorkflowLoader** | `core/workflow_loader.py` | Parses `project.yaml` into typed dataclasses with clear validation errors |
| **ExecutionTrace** | `core/execution_trace.py` | Accumulates per-step metrics (cost, tokens, turns, elapsed) during a run |
| **reporter** | `observability/reporter.py` | Writes `execution_report.json`; prints cost+token summary table to stdout |
| **skill_loader** | `core/skill_loader.py` | Discovers SKILL.md files, evaluates trigger conditions against payload, injects only matching skills |

---

## Execution Flow

```
python main.py --input input.json
       |
       ▼
1. Load & validate project.yaml
       |  WorkflowLoader parses subagents + workflow DAG
       |
       ▼
2. Create run directory + RunLogger
       |  runs/<project-name>/<UTC-timestamp>/
       |  input.json written immediately
       |  execution.log + execution.jsonl opened
       |
       ▼
3. TaskDispatcher: resolve step order (topological sort)
       |  Steps with no depends_on run first
       |  Steps with depends_on wait for their dependencies
       |
       ▼
4. For each step (in order):
       |
       +-- Resolve input (Auto-chain mode -- no input: block)
       |     cumulative_state passed wholesale:
       |       all input fields + all prior step outputs
       |       + ctx_iteration  (current iteration number)
       |       + ctx_feedback   (feedback from previous iteration, if any)
       |
       +-- AgentRunner: build system prompt
       |     Step 1: Read agents/<name>.md 
       |     Step 2: Inject matching skills from skills/<agent>/
       |               parse SKILL.md frontmatter -> evaluate triggers
       |               against payload -> inject only matched skills
       |     Step 3: Append any extra context
       |
       +-- ClaudeCliClient: invoke Claude CLI subprocess
       |     claude \
       |       --print \
       |       --output-format json \
       |       --dangerously-skip-permissions \
       |       --model claude-haiku-4-5-20251001 \
       |       --system-prompt "<agent.md + matched skills>" \
       |       --allowedTools "Read,Grep,mcp__..." \
       |       --mcp-config mcp/mcp.json
       |     (JSON payload sent via stdin -- avoids Windows length limits)
       |
       +-- Parse JSON envelope from stdout
       |     result, session_id, total_cost_usd, num_turns, duration_ms
       |     usage: input_tokens, output_tokens,
       |            cache_read_input_tokens, cache_creation_input_tokens
       |
       +-- Extract JSON from agent's text response
       |
       +-- Merge step output -> cumulative_state
       |
       +-- Write intermediate file: <step_id>_iter<N>.json
       |
       +-- Record StepTrace + log via RunLogger
       |
       ▼
5. Evaluate exit condition / retry
       |  Pipeline-level loop: check reviewer.overall_status == "PASS"
       |  Step-level retry:    check per-step exit_condition field
       |
       ▼  (on FAIL with retries remaining)
6. Inject feedback -> repeat from step 4
       |  FeedbackRule extracts reviewer.feedback_for_generator
       |  Fallback: builds structured feedback from failed validation_results[]
       |
       ▼
7. Assemble output.json
       |  Passthrough fields copied from input unchanged
       |  All step outputs merged in topological order
       |  status + confidence_score added
       |
       ▼
8. Write execution_report.json + print summary
       |  Cost, token, turn breakdown per step
       |
       ▼
9. Exit 0 (PASS) or 1 (FAIL)
```

---

## Project Structure

```
claude-cli-orchestrator/          <- claude-cli-orchestrator
|
+-- project.yaml             <- Workflow definition: agents, steps, retry, feedback rules
+-- main.py                  <- CLI entry point (argparse -> WorkflowEngine)
|
+-- agents/                  <- Agent system prompts (Markdown)
|   +-- generator.md         <- Generates YARA-L 2.0 query from SPL input
|   +-- reviewer.md          <- Validates via V1-V5 validators + MCP syntax check
|
+-- product_docs/            <- Platform knowledge read by agents via the Read tool
|   +-- google_secops/
|   |   +-- platform_config.md           MCP tool names, illegal constructs
|   |   +-- json_understanding.md        UDM / Chronicle log format reference
|   |   +-- query_structure/
|   |       +-- secops_query_syntax.md   YARA-L 2.0 dashboard query syntax
|   |       +-- secops_rule_syntax.md    YARA-L 2.0 detection rule syntax
|   +-- splunk_to_google_secops/
|       +-- command_mapping.md           SPL -> YARA-L command translation table
|
+-- mcp/
|   +-- mcp.json             <- MCP server endpoints (actual servers: google-secops-mcp-server,
|                               context-engine)
|
+-- skills/                  <- Trigger-filtered SKILL.md files injected into system prompts
|   +-- <agent_name>/        <- e.g. skills/generator/, skills/reviewer/
|       +-- <category>/
|           +-- SKILL.md     <- Injected only when frontmatter triggers match payload
|
+-- samples/
|   +-- test_input.json      <- Sample: migration_type=query (dashboard query)
|   +-- test_rule_input.json <- Sample: migration_type=rule  (detection rule)
|
+-- runs/                    <- Auto-created; all execution artifacts per run
|   +-- query-generator/
|       +-- <UTC-timestamp>/
|           +-- input.json              Raw input copy
|           +-- generator_iter<N>.json  Per-step intermediate (payload + result + raw_text)
|           +-- reviewer_iter<N>.json
|           +-- output.json             Final assembled output
|           +-- execution_report.json   Full observability report (cost, tokens, trace events)
|           +-- execution.log           Human-readable timestamped run log [RUN] lines
|
+-- logs/                    <- Auto-created; Python root logger output (one file per run)
|   +-- <UTC-timestamp>.log
|
+-- core/
|   +-- orchestrator.py      <- WorkflowEngine: loads project.yaml, drives pipeline
|   +-- task_dispatcher.py   <- Step execution, cumulative state, template resolution, retry, feedback
|   +-- agent_runner.py      <- Builds prompt (base + skills), calls CLI, extracts JSON output
|   +-- cli_client.py        <- Subprocess wrapper: builds claude command, parses envelope
|   +-- run_logger.py        <- Per-run structured logger (execution.log + execution.jsonl + stdout)
|   +-- workflow_loader.py   <- Parses project.yaml into typed dataclasses
|   +-- execution_trace.py   <- StepTrace + ExecutionTrace (cost, tokens, turns, elapsed)
|   +-- skill_loader.py      <- Parses SKILL.md frontmatter, evaluates triggers, injects matches
|   +-- tool_executor.py     <- JSON extraction helpers
|
+-- config/
|   +-- settings.py          <- All paths, env vars, model defaults, agent definitions
|
+-- observability/
|   +-- reporter.py          <- Writes execution_report.json; prints summary table
|
+-- reference_documents/     <- Migration reference guides
|   +-- MIGRATION_TOOL_GUIDE.md   <- Automated tool: all commands, options, and examples
|   +-- MANUAL_MIGRATION_GUIDE.md <- Manual migration: step-by-step for custom migrations
+-- test_claude_cli.py       <- Standalone Claude CLI test script (for debugging)
```

---

## Key Concepts

### Agents (MD-based)

An **agent** is a Markdown file in `agents/`. It contains the agent's full system prompt -- role, instructions, output schema, tool usage guidelines, and product_docs references.

Agents are stateless. Each invocation receives a full JSON payload via stdin (the cumulative state) and returns a JSON object. The agent's context between iterations comes from the payload the orchestrator constructs automatically.

**Current agents (query-generator project):**

| Agent | File | Role |
|---|---|---|
| `generator` | `agents/generator.md` | Translates Splunk SPL -> Google SecOps YARA-L 2.0. Uses `Read`, `Grep`, context-engine MCP for platform knowledge. |
| `reviewer` | `agents/reviewer.md` | Runs V1-V5 validators. V5 calls `google-secops-mcp-server` for live syntax checking. |

### Cumulative State (Auto-Chain)

Steps have no `input:` block in `project.yaml`. Instead, the orchestrator passes the **full cumulative state** to every step automatically:

```
Iteration 1:
  cumulative_state = {<all input.json fields>}
  -> generator receives all input fields
  -> generator outputs: {result, analysis, reasoning, assumptions, warnings}
  -> merged into cumulative_state

  -> reviewer receives: all input fields + generator's {result, analysis, ...}
  -> reviewer outputs: {overall_status, validation_results, feedback_for_generator}
  -> merged into cumulative_state

Iteration 2 (if reviewer FAIL):
  -> generator receives: all of the above + ctx_feedback (reviewer's feedback)
  -> no explicit wiring needed -- everything is available automatically
```

Two injected context keys added on every step invocation:

| Key | Type | Value |
|---|---|---|
| `ctx_iteration` | int | Current pipeline iteration (1-based) |
| `ctx_feedback` | str \| null | Feedback text set by FeedbackRule for this step |

### Skill Loading (Trigger-Filtered)

Skills are supplemental Markdown files in `skills/<agent_name>/` injected into the agent's system prompt **only when their trigger conditions match the runtime payload**.

```
skills/
+-- generator/
    +-- splunk/SKILL.md            <- triggers: {source_platform: splunk}
    +-- google_secops/SKILL.md    <- triggers: {destination_platform: google_secops}
```

**SKILL.md frontmatter format:**

```markdown
---
name: Google SecOps Field Mapping
description: "Field mapping rules for the generator when destination is Google SecOps."
triggers:
  destination_platform: google_secops
---

# Skill content here ...
```

**Trigger rules:**

| Trigger type | Example | Behaviour |
|---|---|---|
| Single value | `destination_platform: google_secops` | Loads when payload key equals value (case-insensitive keys and values) |
| Any-of list | `destination_platform: [google_secops, chronicle]` | Loads when payload value is in the list (case-insensitive) |
| Multiple keys | Two trigger entries | ANY one matching is enough (OR logic) |
| No triggers block | *(absent)* | Always loaded for this agent |
| Agent dir missing | `skills/generator/` does not exist | Nothing injected -- no fallback |
| Key absent from payload | Trigger key not in payload | That condition is skipped; other keys still evaluated |

The base `.md` file is cached once per run. Skills are re-evaluated on every agent call -- cheap (file read + dict check), always payload-correct.

### WorkflowEngine (Orchestrator)

The `WorkflowEngine` (`core/orchestrator.py`) replaces the Supervisor LLM:

1. Reads `project.yaml` to build the agent map and execution DAG
2. Creates a timestamped run directory and instantiates `RunLogger`
3. Delegates execution to `TaskDispatcher`
4. Logs workflow start/complete via `RunLogger`
5. Assembles the final `output.json` from step outputs + passthrough fields

No project-specific Python code is needed. All configuration lives in `project.yaml`.

### Observability

Every run produces three output streams:

**1. Console `[RUN]` lines** -- live during execution:
```
[RUN]
[RUN] ==============================================================================
[RUN] >> WORKFLOW START  |  query-generator  |  claude-haiku-4-5-20251001
[RUN]   run_id  : 20260422T014407Z
[RUN]   run_dir : runs/query-generator/20260422T014407Z
[RUN]   input   : 22 keys -- source_platform, destination_platform, migration_type, destination_metadata, summary, intent, use_case, parsed_json ...
[RUN] ==============================================================================
[RUN]
[RUN] ---- Iteration 1 / 3 --------------------------------------------
[RUN]
[RUN] >> [generator] STARTING
[RUN]   mode    : auto-chain  |  payload: 23 keys
[RUN]   tools   : Read, Grep, mcp__context-engine__context_engine_agent
[RUN]   input   : {"source_platform": "splunk", "destination_platform": "google_secops", ...}
[RUN]
[RUN] [OK] [generator] COMPLETE  turns=7  cost=$0.2290  in=28  out=5768  cache_r=173646  elapsed=335.1s
[RUN]   output_keys : ['analysis', 'reasoning', 'assumptions', 'warnings', 'result']
[RUN]   output      : {"analysis": "STEP 1 -- Detection objective: ..."}
[RUN]
[RUN] >> [reviewer] STARTING
[RUN]   mode    : auto-chain  |  payload: 28 keys
[RUN]   tools   : 4 tools
[RUN]             - Read
[RUN]             - Grep
[RUN]             - mcp__google-secops-mcp-server__validate_dashboard_query
[RUN]             - mcp__google-secops-mcp-server__validate_rule
[RUN]   input   : {"source_platform": "splunk", ...}
[RUN]
[RUN] [OK] [reviewer] COMPLETE  turns=2  cost=$0.0232  in=8  out=1798  cache_r=38792  elapsed=24.6s
[RUN]   output_keys : ['overall_status', 'all_passed', 'iteration', 'validation_results', 'failed_validators', 'feedback_for_generator']
[RUN]   output      : {"overall_status": "PASS", ...}
[RUN]
[RUN] [OK] EXIT CONDITION MET  step=reviewer  overall_status="PASS" == "PASS"  -> PASS
[RUN]
[RUN] ==============================================================================
[RUN] >> WORKFLOW COMPLETE  |  PASS [OK]  |  iterations=1  cost=$0.2522  elapsed=359.7s
[RUN]   tokens  : in=36  out=7566  turns=9
[RUN]   report  : runs/query-generator/20260422T014407Z/execution_report.json
[RUN] ==============================================================================
```

On FAIL + retry, you also see:
```
[RUN] [X] EXIT CONDITION  step=reviewer  overall_status="FAIL" != "PASS"  -> RETRY
[RUN] ** FEEDBACK -> [generator]  source=[reviewer]  1050 chars
```

**2. `runs/<name>/<ts>/execution.log`** -- identical `[RUN]` lines with a UTC timestamp prefix on every line, written to the run directory.

**3. `runs/<name>/<ts>/execution_report.json`** -- full machine-readable observability record:

```json
{
  "run_id": "20260422T014407Z",
  "workflow": "query-generator",
  "model": "claude-haiku-4-5-20251001",
  "started_at": "2026-04-22T01:44:07.075851+00:00",
  "completed_at": "2026-04-22T01:50:06.731171+00:00",
  "final_status": "PASS",
  "total_iterations": 1,
  "cost": {
    "total_usd": 0.252156,
    "by_step": { "generator": 0.22899, "reviewer": 0.023166 }
  },
  "usage": {
    "total_turns": 9,
    "total_elapsed_sec": 359.64,
    "turns_by_step": { "generator": 7, "reviewer": 2 },
    "total_input_tokens": 36,
    "total_output_tokens": 7566,
    "total_cache_read_tokens": 212438,
    "total_cache_creation_tokens": 22977
  },
  "steps": [
    {
      "step_id": "generator",
      "iteration": 1,
      "agent": "generator",
      "success": true,
      "cost_usd": 0.22899,
      "num_turns": 7,
      "elapsed_sec": 335.07,
      "timestamp": "2026-04-22T01:49:42.147029+00:00",
      "prompt_preview": "# Generator Instructions -- Destination Query Generator ...",
      "output_keys": ["analysis", "reasoning", "assumptions", "warnings", "result"],
      "error": null,
      "tokens": { "input": 28, "output": 5768, "cache_read": 173646, "cache_creation": 14746 },
      "duration_ms": 331819
    }
  ],
  "trace": [
    { "event": "iteration_start", "timestamp": "...", "iteration": 1 },
    { "event": "step_start",      "timestamp": "...", "step": "generator", "iteration": 1, "attempt": 1, "input_mode": "auto-chain" },
    { "event": "step_complete",   "timestamp": "...", "step": "generator", "iteration": 1, "attempt": 1, "success": true, "elapsed_sec": 335.07 },
    { "event": "step_start",      "timestamp": "...", "step": "reviewer",  "iteration": 1, "attempt": 1, "input_mode": "auto-chain" },
    { "event": "step_complete",   "timestamp": "...", "step": "reviewer",  "iteration": 1, "attempt": 1, "success": true, "elapsed_sec": 24.58 },
    { "event": "exit_condition_true", "timestamp": "...", "iteration": 1 }
  ]
}
```

**4. Printed summary** -- automatically displayed after every run:

```
==========================================================================================
  Workflow:   query-generator          Status: PASS [OK]
  Model:      claude-haiku-4-5-20251001
  Report:     runs/query-generator/20260422T014407Z/execution_report.json
==========================================================================================

  Execution Trace
  ------------------------------------------------------------------------------------------
  iter  step                 turns       cost   in_tok  out_tok  cache_r   elapsed  ok
     1  generator                7    $0.2290       28     5768   173646    335.1s  [OK]
     1  reviewer                 2    $0.0232        8     1798    38792     24.6s  [OK]
  ------------------------------------------------------------------------------------------
           TOTAL                 9    $0.2522       36     7566   212438    359.7s
==========================================================================================
```

> There are no `/cost` or `/context` slash commands. The summary prints automatically at the end of every `python main.py` run.

---

## How to Run

### Prerequisites

- **Python 3.10+** (standard library only -- no `pip install` required beyond `PyYAML`)
- **Claude CLI** authenticated:
  ```bash
  claude --version     # confirm version
  claude               # confirm auth (opens interactive session once)
  ```
- **MCP servers** running (for MCP tool calls):

  | Server | URL | Transport | Used by |
  |---|---|---|---|
  | `google-secops-mcp-server` | `http://10.50.12.43:8000/sse` | SSE | Reviewer -- V5 live syntax check |
  | `context-engine` | `http://10.5.50.275:8090/mcp` | HTTP (Streamable) | Generator -- platform knowledge |

### Commands

```bash
cd claude-cli-orchestrator

# Validate config + input without calling Claude (fast check)
python main.py --input samples/test_input.json --dry-run

# Run the pipeline (default model: claude-haiku-4-5-20251001)
python main.py --input samples/test_input.json

# Run with verbose logging (shows tool calls, MCP responses)
python main.py --input samples/test_input.json --verbose

# Override the model
python main.py --input samples/test_input.json --model claude-sonnet-4-6

# Save output to a custom path (also written to runs/ automatically)
python main.py --input samples/test_input.json --output /tmp/result.json

# Pass input as inline JSON
python main.py --json '{"source_platform":"splunk","destination_platform":"google_secops",...}'

# Disable skill injection (debugging)
python main.py --input samples/test_input.json --no-skills
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_MODEL` | `claude-haiku-4-5-20251001` | Model for all agents |
| `LOG_LEVEL` | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING` |
| `VERBOSE` | `0` | Set to `1` for debug output (same as `--verbose`) |

---

## project.yaml Reference

The `WorkflowEngine` reads `project.yaml` at startup. No Python changes are needed to configure a new workflow.

### Current project.yaml (query-generator)

```yaml
name: query-generator
display_name: "Query Generator"
description: "Converts Splunk SPL queries / detection rules to Google SecOps YARA-L 2.0."

subagents:
  - name: generator
    prompt_file: agents/generator.md
    description: "Generates YARA-L 2.0 from SPL using platform docs and field mappings."
    model: sonnet
    tools:
      - Read
      - Grep
      - mcp__context-engine__context_engine_agent

  - name: reviewer
    prompt_file: agents/reviewer.md
    description: "Validates generated YARA-L via V1-V5 validators and MCP syntax check."
    model: sonnet
    tools:
      - Read
      - Grep
      - mcp__google-secops-mcp-server__validate_dashboard_query
      - mcp__google-secops-mcp-server__validate_rule

workflow:
  max_iterations: 3

  steps:
    - id: generator
      agent: generator
      # No input: block -> auto-chain mode:
      # receives full cumulative_state + ctx_iteration + ctx_feedback

    - id: reviewer
      agent: reviewer
      depends_on: [generator]
      # receives cumulative_state (includes generator's result, analysis, etc.)

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
        field: feedback_for_generator
      to:
        step: generator
        as: ctx_feedback
      fallback: generic_validation_feedback

  passthrough_fields:
    - source_platform
    - destination_platform
    - migration_type
    - field_mappings
    - source_metadata
    - summary
    - intent
    - use_case
    - workflow_id
```

### Three Workflow Patterns

**Pattern A -- Single agent, no retry**
*(Example: source-analyzer -- one agent, linear, no loop)*
```yaml
workflow:
  steps:
    - id: analyzer
      agent: analyzer
      # auto-chain: receives full input payload
  passthrough_fields: [workflow_id, source_platform, app_name]
```

**Pattern B -- Pipeline-level retry loop with feedback**
*(Example: query-generator -- generator -> reviewer -> loop until PASS)*
```yaml
workflow:
  max_iterations: 3

  steps:
    - id: generator
      agent: generator
      # auto-chain: cumulative_state + ctx_iteration + ctx_feedback

    - id: reviewer
      agent: reviewer
      depends_on: [generator]
      # auto-chain: cumulative_state includes generator's result automatically

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
        field: feedback_for_generator
      to:
        step: generator
        as: ctx_feedback
      fallback: generic_validation_feedback
```

**Pattern C -- Step-level retries**
*(Example: field-mapper -- field-extractor retries independently, field-mapper retries independently)*
```yaml
workflow:
  steps:
    - id: field-extractor
      agent: field-extractor
      max_retries: 3
      exit_condition:
        field: status
        equals: "PASS"
      feedback:
        field: issues

    - id: field-mapper
      agent: field-mapper
      depends_on: [field-extractor]
      max_retries: 3
      exit_condition:
        field: overall_status
        equals: "PASS"
      feedback:
        field: issues
```

---

## How to Add a New Agent to an Existing Project

**Step 1 -- Create the agent prompt file:**
```
agents/my_agent.md
```
Include: `## Role`, `## Instructions`, `## Output Schema` (must return valid JSON), `## Available Tools`.

**Step 2 -- Register in `project.yaml` subagents:**
```yaml
subagents:
  - name: my-agent
    prompt_file: agents/my_agent.md
    description: "What it does."
    model: sonnet
    tools:
      - Read
      - Grep
```

**Step 3 -- Add a step in `workflow.steps`:**
```yaml
workflow:
  steps:
    - id: my_agent
      agent: my-agent
      depends_on: [previous_step]
      # auto-chain: receives cumulative_state automatically
```

No Python changes required.

---

## How to Add a New MCP Tool

**Step 1 -- Register the server in `mcp/mcp.json`:**
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
Use `"type": "sse"` for SSE servers.  
Use `"type": "http"` for Streamable HTTP servers (POST to `/mcp`).

**Step 2 -- Add the tool to the agent in `project.yaml`:**
```yaml
subagents:
  - name: reviewer
    tools:
      - mcp__google-secops-mcp-server__validate_dashboard_query
      - mcp__google-secops-mcp-server__validate_rule
```

**Step 3 -- Document it in the agent's `.md` prompt:**
```markdown
## Available MCP Tools
- `mcp__google-secops-mcp-server__validate_dashboard_query(query)` -- call this for V5 syntax validation.
```

No Python changes required.

---

## How Claude CLI Is Invoked

Every agent call in `core/cli_client.py` builds and executes:

```bash
claude \
  --print \                                      # Non-interactive: exit after one response
  --output-format stream-json \                  # NDJSON stream: thinking blocks + final result
  --verbose \                                    # Required by Claude CLI when using stream-json with --print
  --dangerously-skip-permissions \               # Suppress approval prompts (automation mode)
  --model claude-haiku-4-5-20251001 \            # Configurable via CLAUDE_MODEL or --model
  --system-prompt "<agent.md + matched skills>" \# Full system prompt; debug log truncates to 500 chars per item
  --allowedTools "Read,Grep,mcp__..." \          # Tool allowlist from project.yaml subagent
  --mcp-config "/abs/path/to/mcp/mcp.json"      # MCP server endpoints
  # JSON payload (cumulative_state) sent via stdin -- avoids Windows command-line length limits
```

**CLI debug logging** (when `--verbose` is set): Each argument is logged on its own line, truncated individually to 500 chars. Long items like `--system-prompt` are trimmed; short items like `--model` and `--allowedTools` appear in full.

**Stream output (`--output-format stream-json`):** Claude CLI emits one JSON object per line (NDJSON). `cli_client._parse_stream()` collects two things from the stream:

1. Thinking blocks — `stream_event` wrappers around `content_block_delta` events with `"type": "thinking_delta"`.
2. Final result envelope — the last line with `"type": "result"`:

```json
{
  "type":           "result",
  "subtype":        "success",
  "result":         "<agent text response containing JSON>",
  "session_id":     "uuid-...",
  "total_cost_usd": 0.1281,
  "num_turns":      8,
  "is_error":       false,
  "duration_ms":    193500,
  "usage": {
    "input_tokens":                58,
    "output_tokens":               7467,
    "cache_read_input_tokens":     255860,
    "cache_creation_input_tokens": 14000
  }
}
```

All token/cost/turn fields are extracted and recorded in `execution_report.json`.

---

## Example: Query Generator End-to-End

**Input** (`samples/test_input.json`):
```json
{
  "source_platform":      "splunk",
  "destination_platform": "google_secops",
  "migration_type":       "query",
  "summary":              "Count failed Windows login attempts per source IP over 1 hour",
  "intent":               "Detect brute-force login attacks",
  "field_mappings": [
    { "source_field": "src_ip",    "destination_field": "principal.ip" },
    { "source_field": "EventCode", "destination_field": "metadata.product_event_type" }
  ]
}
```

**Workflow (query-generator):**
```
generator ---> reviewer ---> PASS -> output.json
    ↑               |
    +-- ctx_feedback ┘  (on FAIL, up to 3 iterations)
```

**Run:**
```bash
python main.py --input samples/test_input.json
```

**Artifacts written:**
```
runs/query-generator/20260422T014407Z/
+-- input.json               <- copy of input
+-- generator_iter1.json     <- generator payload + YARA-L output
+-- reviewer_iter1.json      <- V1-V5 validation results
+-- generator_iter2.json     <- generator with feedback applied (if retry)
+-- reviewer_iter2.json
+-- output.json              <- final assembled output
+-- execution_report.json    <- cost, tokens, trace events (machine-readable)
+-- execution.log            <- timestamped [RUN] lines (human-readable)
```

**Output excerpt:**
```json
{
  "result": "events:\n  metadata.log_type = \"WINDOWS_SECURITY\"\n  ...",
  "overall_status": "PASS",
  "status": "PASS",
  "confidence_score": 1.0,
  "source_platform": "splunk",
  "destination_platform": "google_secops"
}
```

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `project.yaml not found` | Wrong working directory | `cd claude-cli-orchestrator` before running |
| `agent 'X' not in subagents` | Step references unknown agent | Check `agent:` in step matches a `name:` in `subagents:` |
| `prompt file not found` | Wrong `prompt_file` path | Use `agents/filename.md` (relative to project root) |
| `cost_usd` always 0.0 | Old Claude CLI version | Ensure CLI returns `total_cost_usd` in JSON envelope |
| `UnicodeDecodeError` | Windows cp1252 encoding | Already fixed -- `cli_client.py` uses `encoding="utf-8"` |
| Step always returns FAIL | Wrong exit condition field name | Check agent's JSON output keys vs `exit_condition.field` |
| Feedback not injected | `feedback_for_generator` field null | Add `fallback: generic_validation_feedback` to feedback rule |
| MCP tool not available | Server unreachable or wrong transport | Check server URL; use `"type": "http"` for Streamable HTTP |
| Skills not injected | `skills/<agent>/` dir missing | Create agent-specific dir -- no global fallback by design |
| Skill injected when it shouldn't | Trigger conditions too broad | Narrow the `triggers:` block -- remember OR logic: any matching key loads the skill |
| `NoneType has no attribute strip` | Claude CLI returned no output | Check `--verbose` for stderr; MCP server may have crashed |

### Debug Tips

```bash
# Validate config without running Claude
python main.py --input samples/test_input.json --dry-run

# Full debug logging
python main.py --input samples/test_input.json --verbose

# Inspect a failed step
python -c "
import json; from pathlib import Path
d = json.loads(Path('runs/query-generator/<ts>/generator_iter1.json').read_text(encoding='utf-8'))
print('success:', d['success'])
print('error:',   d.get('error'))
print('keys:',    list((d.get('result') or {}).keys()))
"

# Check if MCP V5 syntax check was called
python -c "
import json; from pathlib import Path
d = json.loads(Path('runs/query-generator/<ts>/reviewer_iter1.json').read_text(encoding='utf-8'))
v5 = next(v for v in d['result']['validation_results'] if 'Syntax' in v['validator'])
print('V5 valid:', v5['is_valid'])
print('details:', v5.get('details', {}))
"

# Stream execution events from the report trace
python -c "
import json; from pathlib import Path
r = json.loads(Path('runs/query-generator/<ts>/execution_report.json').read_text(encoding='utf-8'))
for e in r['trace']:
    print(e['timestamp'], e['event'], e.get('step',''))
"
```

### Common Errors

**`AttributeError: 'NoneType' object has no attribute 'strip'`**  
Claude CLI exited without producing stdout. Causes: MCP server crashed mid-response, CLI timeout hit (increase `AGENT_TIMEOUT` in `config/settings.py`).

**`JsonExtractionError: no JSON object found in response`**  
Agent returned plain text. Check the agent `.md` output schema -- it must instruct the agent to return ONLY a JSON object.

**`ValueError: Step 'X' depends_on unknown step 'Y'`**  
A `depends_on` entry references a step ID that doesn't exist. Check spelling in `project.yaml`.

---

## Configuration Reference

| Setting | Location | Default | Override |
|---|---|---|---|
| Model | `config/settings.py` | `claude-haiku-4-5-20251001` | `--model` flag or `CLAUDE_MODEL` env |
| Agent timeout | `config/settings.py` | `1200s` (20 min) | Edit `AGENT_TIMEOUT` |
| Log level | `config/settings.py` | `INFO` | `LOG_LEVEL` env or `--verbose` |
| Max iterations | `project.yaml` | `1` | `workflow.max_iterations` |
| Retry per step | `project.yaml` | `1` | `steps[].max_retries` |
| MCP servers | `mcp/mcp.json` | `google-secops-mcp-server`, `context-engine` | Edit endpoints |

---

## Reference Documents

The `reference_documents/` directory contains two migration guides covering different paths for converting an AgentWeave project to `claude-cli-orchestrator`.

### Migration Tool Guide — `reference_documents/MIGRATION_TOOL_GUIDE.md`

Use this guide when you want to automate the migration with a single command.

The migration tool (`tools/migrate`) runs a 7-stage pipeline that reads an AgentWeave project and produces a fully runnable `claude-cli-orchestrator` project — including the engine bundle, copied agents, MCP config, and a generated `project.yaml` workflow block.

**What the guide covers:**

- How the 7-stage pipeline works (validate → scaffold → classify → LLM extract → assemble → validate → report)
- Full command reference: all positional arguments and options (`--dest`, `--model`, `--force`, `--verbose`, `--skip-llm`)
- Model selection table (Haiku / Sonnet / Opus) with speed, quality, and cost trade-offs
- Usage examples: basic run, force overwrite, model override, skip-LLM scaffold mode, full verbose run
- Reading the console log and exit codes (`0` = clean, `1` = TODOs, `2` = errors)
- Files generated in the destination directory (including `migration_report.md`)
- Understanding `migration_report.md` sections (summary, extracted JSON, TODOs, validation, next steps)
- Key features: hybrid LLM/deterministic extraction, confidence-gated `# TODO` markers, graceful fallback, three-layer validation, skills auto-copy
- Post-migration validation and run commands
- Troubleshooting table for common tool errors

**Quick start:**

```bash
cd claude-cli-orchestrator
python -m tools.migrate ../agentweave/projects/my-project \
    --dest ../migrated_projects/my-project
```

---

### Manual Migration Guide — `reference_documents/MANUAL_MIGRATION_GUIDE.md`

Use this guide when you prefer to migrate a project step by step by hand, or when you need to customize the output beyond what the automated tool produces (e.g., after `--skip-llm`, or for projects with unusual supervisor patterns).

**What the guide covers:**

- What changes vs. what stays the same (agent `.md` files, `mcp.json`, `subagents:` block are reused as-is; supervisor.md is replaced by the `workflow:` block)
- Migration checklist with four steps
- **Step 1** — Identifying your workflow pattern (A / B / C) by reading `supervisor.md`, with a four-question framework
- **Pattern A** — Single agent, runs once; template and customization instructions
- **Pattern B** — Pipeline-level retry loop with feedback; exit condition, feedback routing, passthrough fields
- **Pattern C** — Independent step-level retries; `max_retries`, per-step `exit_condition`, template mode `input:` blocks
- **Step 2** — Updating `project.yaml`: fixing `prompt_file` paths, adding the `workflow:` block, deleting `supervisor.md`
- **Step 3** — Migrating optional assets: `mcp.json`, `product_docs/`, `skills/` (including SKILL.md frontmatter and trigger rules)
- **Step 4** — Test and run: dry-run validation, full run, reading output artifacts
- Reference material: auto-chain vs. template mode, template expression syntax, `supervisor.md` → `project.yaml` mapping table, full `project.yaml` template
- What runs after migration: files read, files written, console output format, summary table
- Adding a new MCP tool (three-step process, no Python required)
- Troubleshooting table for common migration errors

**When to use this guide instead of the tool:**

| Situation | Guide |
|---|---|
| First-time migration of a standard project | Migration Tool Guide |
| Project has an unusual or complex `supervisor.md` | Manual Migration Guide |
| Tool ran with `--skip-llm` and produced `# TODO` stubs | Manual Migration Guide (fill in the stubs) |
| Want to understand what the tool produces | Manual Migration Guide (explains every field) |
| Need to restructure skills or customize `project.yaml` beyond tool output | Manual Migration Guide |
