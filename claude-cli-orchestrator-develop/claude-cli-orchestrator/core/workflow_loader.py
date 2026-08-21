"""
workflow_loader.py -- Parse project.yaml into typed WorkflowDef objects.

Validates required fields and raises clear errors for misconfiguration so
that onboarding mistakes surface immediately at startup, not mid-run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Typed dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SubagentDef:
    """One subagent entry from project.yaml `subagents:` list."""
    name:        str
    prompt_file: str          # relative to project root, e.g. "agents/generator.md"
    description: str  = ""
    model:       str  = "sonnet"
    tools:       list[str] = field(default_factory=list)


@dataclass
class StepCondition:
    """A single field comparison used as exit or trigger condition."""
    step:       str | None = None   # which step's output to inspect; None = current step
    field:      str        = ""     # dot-path into the output dict, e.g. "overall_status"
    equals:     str | None = None
    not_equals: str | None = None


@dataclass
class FeedbackRule:
    """
    Rule for injecting feedback between pipeline iterations.

    when       : condition that must be True to inject feedback
    from_step  : step whose output provides the feedback text
    from_field : dot-path field inside that step's output
    to_step    : step that will receive the feedback on the next iteration
    as_field   : key name under which feedback is injected (ctx.feedback.<to_step>)
    fallback   : name of a built-in fallback function used when from_field is null
    """
    when:       StepCondition
    from_step:  str
    from_field: str
    to_step:    str
    as_field:   str
    fallback:   str | None = None


@dataclass
class StepDef:
    """
    One step in the workflow.

    Pipeline-level loop (query-generator pattern):
      max_retries = 1 (default), exit_condition = None
      The outer pipeline loop in WorkflowEngine controls retries.

    Step-level retry loop (field-mapper pattern):
      max_retries > 1, exit_condition set
      The TaskDispatcher retries this step independently.
    """
    id:             str
    agent:          str
    input:          dict[str, Any]        = field(default_factory=dict)
    depends_on:     list[str]             = field(default_factory=list)
    max_retries:    int                   = 1
    exit_condition: StepCondition | None  = None
    # Field to extract from step output as feedback text for next retry attempt.
    feedback_field: str | None            = None


@dataclass
class WorkflowDef:
    """Full parsed workflow definition from project.yaml."""
    name:               str
    display_name:       str
    description:        str
    subagents:          dict[str, SubagentDef]   # keyed by subagent name
    steps:              list[StepDef]
    max_iterations:     int                       = 1
    exit_condition:     StepCondition | None      = None
    feedback_rules:     list[FeedbackRule]        = field(default_factory=list)
    passthrough_fields: list[str]                 = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------

def load_workflow(project_yaml_path: Path) -> WorkflowDef:
    """
    Parse project.yaml -> WorkflowDef.

    Raises
    ------
    FileNotFoundError  if the file does not exist.
    ValueError         if required fields are missing or malformed.
    """
    if not project_yaml_path.exists():
        raise FileNotFoundError(
            f"project.yaml not found: {project_yaml_path}\n"
            "Create one using the template in MIGRATION_GUIDE.md."
        )

    with project_yaml_path.open(encoding="utf-8-sig") as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        raise ValueError("project.yaml must be a YAML mapping at the top level.")

    # ---- Subagents ----
    subagents: dict[str, SubagentDef] = {}
    for sa in raw.get("subagents", []):
        _require(sa, "name",        "each subagent entry")
        _require(sa, "prompt_file", f"subagent '{sa.get('name', '?')}'")
        name = sa["name"]
        subagents[name] = SubagentDef(
            name=name,
            prompt_file=sa["prompt_file"],
            description=sa.get("description", ""),
            model=sa.get("model", "sonnet"),
            tools=sa.get("tools", []),
        )

    # ---- Workflow block ----
    wf = raw.get("workflow")
    if not wf or not isinstance(wf, dict):
        raise ValueError(
            "project.yaml must contain a 'workflow:' block. "
            "See MIGRATION_GUIDE.md for the required format."
        )

    # ---- Steps ----
    steps_raw = wf.get("steps", [])
    if not steps_raw:
        raise ValueError("workflow.steps must contain at least one step.")

    steps: list[StepDef] = []
    for s in steps_raw:
        _require(s, "id",    "each workflow step")
        _require(s, "agent", f"step '{s.get('id', '?')}'")

        agent_name = s["agent"]
        if agent_name not in subagents:
            raise ValueError(
                f"Step '{s['id']}' references agent '{agent_name}' "
                f"which is not listed in subagents. "
                f"Available: {list(subagents.keys())}"
            )

        fb_raw = s.get("feedback")
        feedback_field = (
            fb_raw.get("field") if isinstance(fb_raw, dict) else None
        )

        steps.append(StepDef(
            id=s["id"],
            agent=agent_name,
            input=s.get("input", {}),
            depends_on=s.get("depends_on", []),
            max_retries=int(s.get("max_retries", 1)),
            exit_condition=_parse_condition(s.get("exit_condition")),
            feedback_field=feedback_field,
        ))

    # ---- Pipeline-level exit condition ----
    pipeline_exit = _parse_condition(wf.get("exit_condition"))

    # ---- Feedback rules ----
    feedback_rules: list[FeedbackRule] = []
    for fb in wf.get("feedback", []):
        _require(fb, "when", "each feedback rule")
        _require(fb, "from", "each feedback rule")
        _require(fb, "to",   "each feedback rule")

        fb_from = fb["from"]
        fb_to   = fb["to"]
        _require(fb_from, "step",  "feedback.from")
        _require(fb_from, "field", "feedback.from")
        _require(fb_to,   "step",  "feedback.to")
        _require(fb_to,   "as",    "feedback.to")

        feedback_rules.append(FeedbackRule(
            when=_parse_condition(fb["when"]),
            from_step=fb_from["step"],
            from_field=fb_from["field"],
            to_step=fb_to["step"],
            as_field=fb_to["as"],
            fallback=fb.get("fallback"),
        ))

    return WorkflowDef(
        name=raw.get("name", "unnamed"),
        display_name=raw.get("display_name", raw.get("name", "")),
        description=raw.get("description", ""),
        subagents=subagents,
        steps=steps,
        max_iterations=int(wf.get("max_iterations", 1)),
        exit_condition=pipeline_exit,
        feedback_rules=feedback_rules,
        passthrough_fields=wf.get("passthrough_fields", []),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_condition(raw: Any) -> StepCondition | None:
    """Parse a condition dict into a StepCondition. Returns None if absent."""
    if not raw or not isinstance(raw, dict):
        return None
    return StepCondition(
        step=raw.get("step"),
        field=raw.get("field", ""),
        equals=str(raw["equals"])      if raw.get("equals")      is not None else None,
        not_equals=str(raw["not_equals"]) if raw.get("not_equals") is not None else None,
    )


def _require(mapping: dict, key: str, context: str) -> None:
    """Raise ValueError with a clear message if key is missing from mapping."""
    if key not in mapping:
        raise ValueError(
            f"Missing required key '{key}' in {context}. "
            "Check your project.yaml against MIGRATION_GUIDE.md."
        )
