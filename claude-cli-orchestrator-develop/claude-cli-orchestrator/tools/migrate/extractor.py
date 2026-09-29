"""
extractor.py -- Step 4 of the migration pipeline.

Invokes Claude CLI (via the orchestrator's existing ClaudeCliClient) to read
the AgentWeave supervisor.md + project.yaml + agent output schemas, and returns
a strict JSON object describing the extracted workflow.

The JSON contract is in schemas/workflow_extraction.schema.json. This module
validates the LLM response against that schema before returning.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.cli_client import ClaudeCliClient

from tools.migrate.classifier import ClassificationResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

MODULE_DIR    = Path(__file__).resolve().parent
PROMPT_PATH   = MODULE_DIR / "prompts" / "extractor.md"
SCHEMA_PATH   = MODULE_DIR / "schemas" / "workflow_extraction.schema.json"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ExtractionResult:
    """What the assembler consumes."""
    data:           dict                        # raw JSON matching the schema
    pattern:        str                         # convenience accessor
    confidence:     float
    notes:          list[str]                   = field(default_factory=list)
    raw_response:   str                         = ""
    schema_valid:   bool                        = True
    schema_errors:  list[str]                   = field(default_factory=list)
    cost_usd:       float                       = 0.0
    num_turns:      int                         = 0


class ExtractorError(RuntimeError):
    """Fatal failure to extract (e.g., CLI error, JSON parse failure after retry)."""


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

class Extractor:
    """Calls Claude CLI, parses JSON, validates against the schema."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        timeout: int = 300,
        verbose: bool = False,
    ):
        self.client = ClaudeCliClient(model=model, timeout=timeout, verbose=verbose)
        self._system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        self._schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def extract(
        self,
        supervisor_md_path:   Path,
        source_yaml_path:     Path,
        agent_paths:          dict[str, Path],         # {agent_name: path/to/agent.md}
        classification_hint:  ClassificationResult,
    ) -> ExtractionResult:
        """Run one extraction. Raises ExtractorError on fatal failure."""

        user_message = self._build_user_message(
            supervisor_md_path,
            source_yaml_path,
            agent_paths,
            classification_hint,
        )

        logger.info("Extractor: calling Claude CLI with model=%s", self.client.model)
        response = self.client.run(
            system_prompt=self._system_prompt,
            user_message=user_message,
            allowed_tools=[],              # No tools needed; supervisor.md is in the prompt.
            mcp_config_path=None,
        )

        data, raw_text = self._parse_json(response.raw_text)
        schema_valid, schema_errors = self._validate_schema(data)

        return ExtractionResult(
            data=data,
            pattern=data.get("pattern", "unknown"),
            confidence=float(data.get("confidence", 0.0)),
            notes=list(data.get("notes", [])),
            raw_response=raw_text,
            schema_valid=schema_valid,
            schema_errors=schema_errors,
            cost_usd=response.cost_usd,
            num_turns=response.num_turns,
        )

    # ------------------------------------------------------------------
    # User-message construction
    # ------------------------------------------------------------------

    def _build_user_message(
        self,
        supervisor_md_path: Path,
        source_yaml_path:   Path,
        agent_paths:        dict[str, Path],
        classification_hint: ClassificationResult,
    ) -> str:
        """
        The LLM receives:
          - the full supervisor.md
          - the full source project.yaml
          - each subagent's Output Schema section (or a best-effort snippet)
          - the heuristic classifier's verdict as a hint
        """
        supervisor_text = supervisor_md_path.read_text(encoding="utf-8", errors="replace")
        source_yaml     = source_yaml_path.read_text(encoding="utf-8", errors="replace")

        schema_blocks = []
        for agent_name, path in agent_paths.items():
            snippet = self._extract_output_schema(path)
            schema_blocks.append(
                f"### Agent: {agent_name} (from {path.name})\n"
                f"{snippet}\n"
            )

        hint_block = (
            f"pattern: {classification_hint.pattern}\n"
            f"confidence: {classification_hint.confidence}\n"
            f"iteration_hint: {classification_hint.iteration_hint}\n"
            f"signals:\n  - " + "\n  - ".join(classification_hint.signals)
        )

        return (
            "Extract the workflow from this AgentWeave project.\n\n"
            "<supervisor.md>\n"
            f"{supervisor_text}\n"
            "</supervisor.md>\n\n"
            "<source_project.yaml>\n"
            f"{source_yaml}\n"
            "</source_project.yaml>\n\n"
            "<agent_output_schemas>\n"
            f"{chr(10).join(schema_blocks)}\n"
            "</agent_output_schemas>\n\n"
            "<heuristic_classifier_hint>\n"
            f"{hint_block}\n"
            "</heuristic_classifier_hint>\n\n"
            "Return ONLY the JSON object. No markdown, no commentary."
        )

    @staticmethod
    def _extract_output_schema(agent_md_path: Path) -> str:
        """
        Best-effort: return the '## Output' / '## Output Schema' section.
        Fall back to the first fenced JSON block. Fall back to 'schema not found'.
        """
        text = agent_md_path.read_text(encoding="utf-8", errors="replace")

        # Try to match a ## Output* section up to the next ## heading.
        m = re.search(
            r"(^##+\s*Output[^\n]*\n.*?)(?=\n##\s|\Z)",
            text,
            flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
        )
        if m:
            return m.group(1).strip()

        # Fallback: first fenced JSON block.
        m = re.search(r"```json\s*\n(.*?)```", text, flags=re.DOTALL)
        if m:
            return "```json\n" + m.group(1).strip() + "\n```"

        return "(output schema not found in agent markdown)"

    # ------------------------------------------------------------------
    # JSON parsing (tolerant of markdown fences)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json(raw_text: str) -> tuple[dict, str]:
        """
        Extract a JSON object from the LLM response, tolerant of markdown
        fences or pre/post whitespace. Raises ExtractorError if no JSON found.
        """
        text = raw_text.strip()

        # Strip ```json ... ``` fences if present.
        fence = re.match(r"^```(?:json)?\s*\n(.*)\n```$", text, flags=re.DOTALL)
        if fence:
            text = fence.group(1).strip()

        # Find the first balanced { ... } object.
        start = text.find("{")
        if start == -1:
            raise ExtractorError(f"No JSON object found in response:\n{raw_text[:500]}")

        depth = 0
        end = -1
        for i, ch in enumerate(text[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end == -1:
            raise ExtractorError(f"Unterminated JSON object in response:\n{raw_text[:500]}")

        candidate = text[start : end + 1]
        try:
            return json.loads(candidate), candidate
        except json.JSONDecodeError as e:
            raise ExtractorError(f"JSON parse failed: {e}\npayload: {candidate[:500]}") from e

    # ------------------------------------------------------------------
    # Lightweight schema validation (no external dep; matches our needs)
    # ------------------------------------------------------------------

    def _validate_schema(self, data: dict) -> tuple[bool, list[str]]:
        """
        Minimal schema check. We don't pull in jsonschema to avoid a pip dep.
        We validate the structural invariants the assembler relies on.
        """
        errors: list[str] = []

        required_top = ["pattern", "confidence", "steps", "passthrough_fields", "notes"]
        for key in required_top:
            if key not in data:
                errors.append(f"missing top-level key: {key}")

        if data.get("pattern") not in {"A", "B", "C", "unknown"}:
            errors.append(f"pattern must be A/B/C/unknown, got {data.get('pattern')!r}")

        conf = data.get("confidence")
        if not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
            errors.append(f"confidence must be 0.0-1.0, got {conf!r}")

        steps = data.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append("steps must be a non-empty list")
        else:
            for i, step in enumerate(steps):
                if not isinstance(step, dict):
                    errors.append(f"steps[{i}] is not an object")
                    continue
                for req in ("id", "agent", "confidence"):
                    if req not in step:
                        errors.append(f"steps[{i}] missing required field: {req}")

        pattern = data.get("pattern")
        if pattern == "B":
            if not data.get("pipeline_exit_condition"):
                errors.append("pattern B requires pipeline_exit_condition")
        if pattern == "C":
            for i, step in enumerate(steps or []):
                if step.get("exit_condition") is None:
                    errors.append(f"pattern C: steps[{i}] requires exit_condition")

        pt = data.get("passthrough_fields")
        if not isinstance(pt, list):
            errors.append("passthrough_fields must be a list")

        return (len(errors) == 0), errors
