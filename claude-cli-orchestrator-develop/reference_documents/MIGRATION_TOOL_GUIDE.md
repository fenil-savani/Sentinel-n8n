# Migration Tool Guide

Automate the conversion of an AgentWeave project to a standalone `claude-cli-orchestrator` project with a single command.

> For **manual** migration (without the tool), see `MIGRATION_GUIDE.md`.

---

## What the Tool Does

The migration tool (`tools/migrate`) is a 7-step automated pipeline that reads an AgentWeave project and produces a fully runnable `claude-cli-orchestrator` project in a new directory — including the engine bundle, copied agents, MCP config, and a generated `project.yaml` workflow block.

The heavy lifting is handled by a hybrid approach:
- **Deterministic steps** (no LLM): file copy, subagents block, project metadata
- **LLM-assisted steps** (Claude API): reads `supervisor.md` to extract exit conditions, feedback field names, passthrough fields, and workflow pattern

The result is a complete project ready to run — or a best-effort project with `# TODO` markers in `project.yaml` for any fields the LLM couldn't determine with confidence.

---

## How the Pipeline Works

The tool runs these 7 stages in sequence:

| Stage | What happens |
|---|---|
| **1. Validate source** | Checks that source has `project.yaml`, `agents/supervisor.md`, and at least one subagent `.md` file |
| **2. Scaffold destination** | Creates the output directory; copies the orchestrator engine bundle (`main.py`, `core/`, `config/`, `observability/`); copies agents, `mcp.json`, `product_docs/`, `skills/` |
| **3. Classify pattern** | Heuristic regex scan of `supervisor.md` to detect Pattern A / B / C — no LLM, used as a hint |
| **4. LLM extraction** | Calls Claude API with `supervisor.md` + `project.yaml` + agent output schemas; extracts workflow JSON (exit conditions, feedback routing, passthrough fields, pattern) |
| **5. Assemble project.yaml** | Deterministically renders YAML from the LLM's JSON — the LLM never writes YAML directly; fields with confidence < 0.7 get `# TODO` markers |
| **6. Validate** | Three-layer check: (a) schema check on LLM JSON, (b) cross-reference field names against agent `.md` files, (c) load `project.yaml` through `WorkflowLoader` to catch structural errors |
| **7. Write report** | Writes `migration_report.md` in the destination with full audit trail: pattern, confidence, cost, extracted JSON, TODOs, validation results, next steps |

---

## Prerequisites

- Python 3.11+
- `claude` CLI installed and authenticated (`claude --version` should work)
- Run from inside the `claude-cli-orchestrator/` directory

```bash
cd claude-cli-orchestrator
```

---

## Command Reference

```
python -m tools.migrate <SOURCE> --dest <DEST> [OPTIONS]
```

### Positional Arguments

| Argument | Description |
|---|---|
| `SOURCE` | Path to the AgentWeave project directory (must contain `project.yaml` and `agents/supervisor.md`) |

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--dest PATH` | Path | *(required)* | Output directory for the migrated project. Must not exist unless `--force` is also passed. |
| `--model MODEL` | string | `claude-sonnet-4-6` | Claude model used for LLM extraction. |
| `--force` | flag | off | Overwrite the destination directory if it already exists. |
| `--verbose` | flag | off | Enable debug-level logging (shows full Claude CLI output, signal matching details). |
| `--skip-llm` | flag | off | Skip the LLM extraction step entirely. Produces a stub `project.yaml` with `# TODO` markers for every workflow field. Useful for a quick scaffolded structure without API cost. |

### Model Options

| Model ID | Speed | Quality | Best for |
|---|---|---|---|
| `claude-sonnet-4-6` | Medium | High | Default — best extraction quality |
| `claude-haiku-4-5-20251001` | Fast | Good | Quick iterations, lower cost |
| `claude-opus-4-7` | Slow | Highest | Complex supervisors with ambiguous patterns |

---

## Usage Examples

### Basic migration

```bash
cd claude-cli-orchestrator
python -m tools.migrate ../agentweave/projects/analyzer \
    --dest ../migrated_projects/analyzer
```

### Overwrite an existing output directory

```bash
python -m tools.migrate ../agentweave/projects/analyzer \
    --dest ../migrated_projects/analyzer \
    --force
```

### Use a faster/cheaper model

```bash
python -m tools.migrate ../agentweave/projects/query-generator \
    --dest ../migrated_projects/query-generator \
    --model claude-haiku-4-5-20251001 \
    --force
```

### Use the most capable model for a complex project

```bash
python -m tools.migrate ../agentweave/projects/field-mapper \
    --dest ../migrated_projects/field-mapper \
    --model claude-opus-4-7 \
    --force
```

### Skip the LLM step (scaffold only, no API cost)

```bash
python -m tools.migrate ../agentweave/projects/field-mapper \
    --dest ../migrated_projects/field-mapper \
    --skip-llm \
    --force
```

Produces a working structure with `# TODO` placeholders in `project.yaml`. You then fill in the workflow fields manually using `MIGRATION_GUIDE.md` as a reference.

### Full verbose run

```bash
python -m tools.migrate ../agentweave/projects/query-generator \
    --dest ../migrated_projects/query-generator \
    --model claude-sonnet-4-6 \
    --verbose \
    --force
```

---

## Understanding the Output

### Console log

The tool prints `[migrate]` prefixed progress lines for each pipeline stage:

```
[migrate] ===============================================================
[migrate]  Migration pipeline starting
[migrate]    source : ..\agentweave\projects\field-mapper
[migrate]    dest   : ..\migrated_projects\field-mapper
[migrate]    model  : claude-sonnet-4-6
[migrate]    skip_llm: False
[migrate] ===============================================================
[migrate] [1/7] Source validated + destination scaffolded.
[migrate]        Agents copied: ['field-extractor.md', 'field-mapper.md']
[migrate] [2/7] Heuristic pattern=C confidence=0.85
[migrate] Extractor: calling Claude CLI with model=claude-sonnet-4-6
[migrate] Claude CLI OK | session=... turns=1 cost=$0.0952 in=... out=...
[migrate] [3/7] LLM extraction done: pattern=C confidence=0.90 cost=$0.0952
[migrate] [4/7] Wrote project.yaml (2 TODO markers).
[migrate] [5/7] Validation: 0 errors, 2 warnings
[migrate] [6/7] Wrote migration_report.md at <dest>/migration_report.md
[migrate] [7/7] Migration complete. Open <dest>/migration_report.md for a full summary.
```

### Exit codes

| Code | Meaning | Action required |
|---|---|---|
| `0` | Clean — no errors, no TODOs | Ready to run |
| `1` | Completed with TODOs or warnings | Review `# TODO` markers and validation warnings in `migration_report.md` |
| `2` | Errors — validation failed or pipeline aborted | Fix errors listed in `migration_report.md` before running |

> Exit code `1` is not a failure. It means the project was produced but needs human review. This is normal for complex supervisors.

### Files generated in `<dest>/`

```
<dest>/
├── project.yaml            ← generated workflow (the main output)
├── migration_report.md     ← full audit trail with status, TODOs, raw JSON
├── main.py                 ← orchestrator engine entry point
├── core/                   ← engine runtime (WorkflowEngine, AgentRunner, etc.)
├── config/                 ← engine configuration
├── observability/          ← run logger and report writer
├── agents/                 ← copied agent .md files (supervisor.md excluded)
├── mcp/mcp.json            ← copied MCP server configuration
├── product_docs/           ← copied if present in source
├── skills/                 ← copied if present in source
├── runs/                   ← empty; populated at runtime
├── logs/                   ← empty; populated at runtime
├── samples/                ← empty; add your input.json here
└── .migration_source/      ← stashed source supervisor.md + project.yaml (for reference)
    ├── supervisor.md
    └── project.yaml
```

### migration_report.md

The report is the primary post-run artifact. Open it to understand what the tool did:

| Section | What it tells you |
|---|---|
| **Summary** | Overall status, pattern, LLM confidence, number of errors/warnings, API cost |
| **Files Copied** | Which agents, mcp.json, product_docs, skills were copied; any scaffold warnings |
| **Heuristic Classification** | Which regex signals fired to suggest the pattern (before LLM) |
| **LLM Workflow Extraction** | The LLM's final pattern verdict, confidence, schema validity, and the raw extraction JSON |
| **Open TODOs** | Human-readable list of every `# TODO` inserted into `project.yaml` |
| **Validation** | Errors, warnings, and info from all three validation layers |
| **Next Steps** | Exact commands to validate and run the migrated project |

---

## Key Features

### Hybrid extraction
The tool never asks the LLM to write YAML. Instead, the LLM returns a strict JSON object (validated against `tools/migrate/schemas/workflow_extraction.schema.json`) and the assembler renders deterministic YAML from it. This makes the output predictable and auditable.

### Confidence-gated TODOs
Every extracted field carries a confidence score (0.0–1.0). Fields below `0.7` get a `# TODO` comment in `project.yaml` so the developer knows exactly which fields need human verification.

### Graceful fallback
If the LLM step fails (network error, API timeout, JSON parse failure), the tool falls back to a stub extraction based purely on the heuristic classifier. The pipeline always produces a report even on partial failure.

### Three-layer validation
Before writing the report, the validator runs:
1. **Schema check** — LLM JSON matches the required structure
2. **Cross-reference check** — Every field name referenced in `project.yaml` (exit conditions, feedback fields) is searched in the corresponding agent's `.md` file
3. **Loader check** — `project.yaml` is parsed by the real `WorkflowLoader` to catch structural YAML errors, bad agent references, and invalid DAG edges

### Skills auto-copy with restructure warning
Skills are copied from whatever layout the source uses (`skills/`, `.claude/skills/`, `product_docs/skills/`). If the directory structure doesn't match the orchestrator's expected `skills/<agent_name>/` layout, the report includes a warning with instructions.

---

## After Migration: Validate and Run

Once migration completes:

**1. Review `project.yaml`** — look for `# TODO` markers and fill them in.

**2. Dry-run (no Claude API calls):**
```bash
cd <dest>
python main.py --input samples/input.json --dry-run
```

**3. Full run:**
```bash
python main.py --input samples/input.json
```

Output lands in `runs/<project-name>/<UTC-timestamp>/`:
- `output.json` — final assembled result
- `execution_report.json` — cost, tokens, per-step metrics
- `execution.log` — human-readable run log

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ScaffoldError: Missing supervisor.md` | Source project has no `agents/supervisor.md` | Check source path; supervisor must be at `agents/supervisor.md` or `<project_root>/supervisor.md` |
| `ScaffoldError: Destination already exists` | Output dir exists from a previous run | Add `--force` to overwrite |
| `ExtractorError: No JSON object found` | LLM returned prose instead of JSON | Re-run; or use `--model claude-opus-4-7` for a more instruction-following response |
| `project.yaml failed to load` (validation error) | Structural YAML error or bad agent reference | Open `project.yaml`, check step IDs match `subagents.name` values |
| Validation warning: field not found in agent .md | Exit condition or feedback field name doesn't appear in agent prompt | The field name may be correct but the agent's `.md` doesn't document it — review manually |
| All fields are `# TODO` | `--skip-llm` was used or LLM failed | Fill in manually using `MIGRATION_GUIDE.md`, or re-run without `--skip-llm` |
| Skills warning about directory structure | Source skills not in `skills/<agent_name>/` layout | Restructure manually: move `skills/SKILL.md` → `skills/<agent_name>/SKILL.md` and add frontmatter triggers |
| High cost per migration | Large supervisor.md | Normal — supervisor + agent schemas are sent as context; typical cost is $0.08–$0.12 per project |

---

## What the Tool Does NOT Do

- It does **not** modify the source AgentWeave project in any way
- It does **not** run the migrated project — it only produces the project directory
- It does **not** validate MCP server connectivity or agent `.md` correctness
- It does **not** restructure skills into `skills/<agent_name>/` layout automatically — that requires manual review
- It does **not** delete `supervisor.md` from the source — the `.migration_source/` stash in the destination is a read-only copy

---

## Reference Projects

These three projects are the validated reference examples (tested with LLM extraction):

| Project | Pattern | Confidence | Cost | Errors | Warnings |
|---|---|---|---|---|---|
| `source-analyzer` | A | 0.97 | ~$0.09 | 0 | 0 |
| `field-mapper` | C | 0.90 | ~$0.10 | 0 | 2 (expected — CSV agent) |
| `query-generator` | B | 0.97 | ~$0.10 | 0 | 0 |
