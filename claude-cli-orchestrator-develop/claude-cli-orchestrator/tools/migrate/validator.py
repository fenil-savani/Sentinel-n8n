"""
validator.py -- Step 6 of the migration pipeline.

Runs three validation layers on the migrated project:

  1. Schema check on the LLM extraction JSON (already done in extractor,
     re-surfaced here for the report).
  2. Cross-reference check: every field name referenced in the workflow
     (exit_condition.field, feedback.from_field, per-step feedback field)
     must appear in the referenced agent's output schema.
  3. Load test: attempt to load the generated project.yaml through the
     orchestrator's WorkflowLoader -- catches structural YAML problems
     and missing agent references.

Each check returns a list of issues; caller aggregates them into the report.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ValidationIssue:
    severity: str           # "error" | "warning" | "info"
    source:   str           # "schema" | "crossref" | "loader" | "dryrun"
    message:  str


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def add(self, severity: str, source: str, message: str) -> None:
        self.issues.append(ValidationIssue(severity, source, message))


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class Validator:
    """
    Runs all three validation layers against an already-assembled target
    directory (project.yaml + agents/ + .migration_source/).
    """

    def __init__(self, dest: Path):
        self.dest = Path(dest).resolve()
        self.report = ValidationReport()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(
        self,
        extraction_schema_errors: list[str],
        extraction_data:          dict,
    ) -> ValidationReport:
        self._check_schema(extraction_schema_errors)
        self._check_crossref(extraction_data)
        self._check_loader()
        return self.report

    # ------------------------------------------------------------------
    # Layer 1: schema
    # ------------------------------------------------------------------

    def _check_schema(self, errors: list[str]) -> None:
        for e in errors:
            self.report.add("error", "schema", e)

    # ------------------------------------------------------------------
    # Layer 2: cross-reference agent output schemas
    # ------------------------------------------------------------------

    def _check_crossref(self, data: dict) -> None:
        """
        For every field the workflow references in an agent's output, verify
        the name appears somewhere in that agent's .md. This is a best-effort
        substring check -- if an agent's schema is unstructured prose we warn
        rather than fail.
        """
        agents_dir = self.dest / "agents"
        if not agents_dir.is_dir():
            self.report.add("error", "crossref", f"agents/ not found in {self.dest}")
            return

        agent_texts: dict[str, str] = {}
        for md in agents_dir.glob("*.md"):
            agent_texts[md.stem] = md.read_text(encoding="utf-8", errors="replace")

        # Pipeline exit_condition
        pec = data.get("pipeline_exit_condition")
        if pec:
            self._crossref_field(
                agent_texts, pec.get("step"), pec.get("field"),
                context="pipeline_exit_condition",
            )

        # Pipeline feedback
        pfb = data.get("pipeline_feedback")
        if pfb:
            self._crossref_field(
                agent_texts, pfb.get("from_step"), pfb.get("from_field"),
                context="pipeline_feedback.from_field",
            )

        # Step-level exit conditions / feedback (Pattern C)
        for step in data.get("steps", []):
            ec = step.get("exit_condition")
            if ec:
                self._crossref_field(
                    agent_texts, step.get("agent"), ec.get("field"),
                    context=f"steps[{step.get('id')}].exit_condition.field",
                )
            fb = step.get("feedback_field")
            if fb:
                self._crossref_field(
                    agent_texts, step.get("agent"), fb,
                    context=f"steps[{step.get('id')}].feedback.field",
                )

    def _crossref_field(
        self,
        agent_texts: dict[str, str],
        step_or_agent: str | None,
        field_name:  str | None,
        context:     str,
    ) -> None:
        if not step_or_agent or not field_name:
            return
        # agent id may match step id directly, or we may need to try _ <-> - variants.
        candidates = [
            step_or_agent,
            step_or_agent.replace("-", "_"),
            step_or_agent.replace("_", "-"),
        ]
        text = None
        for c in candidates:
            if c in agent_texts:
                text = agent_texts[c]
                break
        if text is None:
            self.report.add(
                "warning", "crossref",
                f"{context}: agent '{step_or_agent}' has no matching .md file in agents/ "
                f"(have: {', '.join(sorted(agent_texts.keys()))})",
            )
            return

        if not re.search(re.escape(field_name), text):
            self.report.add(
                "warning", "crossref",
                f"{context}: field '{field_name}' not found in {step_or_agent}.md -- "
                "possible LLM hallucination; review before running",
            )

    # ------------------------------------------------------------------
    # Layer 3: load project.yaml through WorkflowLoader
    # ------------------------------------------------------------------

    def _check_loader(self) -> None:
        """
        Run a tiny Python snippet inside the migrated project as a subprocess
        that imports the orchestrator's WorkflowLoader and parses project.yaml.
        This catches structural YAML errors, unknown agent refs, bad DAG edges.
        """
        snippet = (
            "import sys, json\n"
            "from pathlib import Path\n"
            "from core.workflow_loader import load_workflow\n"
            "try:\n"
            "    load_workflow(Path('project.yaml'))\n"
            "    print(json.dumps({'ok': True}))\n"
            "except Exception as e:\n"
            "    print(json.dumps({'ok': False, 'error': f'{type(e).__name__}: {e}'}))\n"
        )
        try:
            proc = subprocess.run(
                [sys.executable, "-c", snippet],
                capture_output=True,
                text=True,
                cwd=str(self.dest),
                timeout=30,
            )
        except Exception as e:
            self.report.add("warning", "loader", f"Could not spawn loader subprocess: {e}")
            return

        out = proc.stdout.strip()
        err = proc.stderr.strip()

        if proc.returncode != 0 and not out:
            self.report.add(
                "error", "loader",
                f"Loader subprocess failed (rc={proc.returncode}):\n{err[:400]}",
            )
            return

        try:
            import json as _json
            result = _json.loads(out)
        except Exception:
            self.report.add(
                "warning", "loader",
                f"Could not parse loader output:\n{out[:400]}",
            )
            return

        if not result.get("ok"):
            self.report.add(
                "error", "loader",
                f"project.yaml failed to load: {result.get('error')}",
            )
        else:
            self.report.add("info", "loader", "project.yaml loads successfully")
