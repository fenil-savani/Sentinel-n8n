"""
reporter.py -- Step 7 of the migration pipeline.

Aggregates everything the pipeline produced -- ScaffoldReport, ClassificationResult,
ExtractionResult, AssemblyResult, ValidationReport -- into a single
migration_report.md in the destination directory.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tools.migrate.assembler import AssemblyResult
from tools.migrate.classifier import ClassificationResult
from tools.migrate.extractor import ExtractionResult
from tools.migrate.scaffolder import ScaffoldReport
from tools.migrate.validator import ValidationReport


class Reporter:
    """Writes migration_report.md to dest/."""

    def __init__(
        self,
        dest:           Path,
        scaffold:       ScaffoldReport,
        classification: ClassificationResult,
        extraction:     ExtractionResult | None,
        assembly:       AssemblyResult | None,
        validation:     ValidationReport,
    ):
        self.dest           = Path(dest).resolve()
        self.scaffold       = scaffold
        self.classification = classification
        self.extraction     = extraction
        self.assembly       = assembly
        self.validation     = validation

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def write(self) -> Path:
        path = self.dest / "migration_report.md"
        path.write_text(self._render(), encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    # Renderer
    # ------------------------------------------------------------------

    def _render(self) -> str:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        blocks: list[str] = []
        blocks.append(self._section_header(ts))
        blocks.append(self._section_summary())
        blocks.append(self._section_files_copied())
        blocks.append(self._section_classification())
        blocks.append(self._section_extraction())
        blocks.append(self._section_todos())
        blocks.append(self._section_validation())
        blocks.append(self._section_next_steps())
        return "\n\n".join(blocks).strip() + "\n"

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def _section_header(self, ts: str) -> str:
        name = (self.assembly.source_name if self.assembly else
                self.scaffold.project_name or "<unnamed>")
        return (
            f"# Migration Report -- {name}\n\n"
            f"- Generated:   {ts}\n"
            f"- Source:      `{self.scaffold.source}`\n"
            f"- Destination: `{self.scaffold.dest}`\n"
        )

    def _section_summary(self) -> str:
        status = self._overall_status()
        lines = [
            "## Summary",
            "",
            f"**Status:** {status}",
            "",
            f"- Pattern detected:  **{self.extraction.pattern if self.extraction else 'n/a'}**",
            f"- LLM confidence:    {self.extraction.confidence:.2f}" if self.extraction else "- LLM confidence:    n/a",
            f"- Heuristic hint:    {self.classification.pattern} (confidence {self.classification.confidence:.2f})",
            f"- Validation errors: {len(self.validation.errors)}",
            f"- Validation warns:  {len(self.validation.warnings)}",
        ]
        if self.extraction:
            lines.append(f"- Extractor cost:    ${self.extraction.cost_usd:.4f} ({self.extraction.num_turns} turns)")
        return "\n".join(lines)

    def _section_files_copied(self) -> str:
        s = self.scaffold
        lines = ["## Files Copied", ""]
        lines.append(f"- Agents: {len(s.agents_copied)} file(s) copied from `agents/`")
        for a in s.agents_copied:
            lines.append(f"  - {a}")
        lines.append(f"- mcp.json:      {'yes' if s.mcp_json_path else 'NOT FOUND in source'}")
        lines.append(f"- product_docs/: {'copied' if s.product_docs else 'not present in source'}")
        lines.append(f"- skills/:       {'copied' if s.skills_copied else 'not present in source'}")
        if s.warnings:
            lines.append("")
            lines.append("**Scaffold warnings:**")
            for w in s.warnings:
                lines.append(f"- {w}")
        return "\n".join(lines)

    def _section_classification(self) -> str:
        c = self.classification
        lines = [
            "## Heuristic Pattern Classification",
            "",
            f"- Pattern:    **{c.pattern}**",
            f"- Confidence: {c.confidence:.2f}",
            f"- Iteration hint: {c.iteration_hint}",
            "",
            "Signals:",
        ]
        for s in c.signals:
            lines.append(f"- {s}")
        return "\n".join(lines)

    def _section_extraction(self) -> str:
        if not self.extraction:
            return "## LLM Workflow Extraction\n\n*Skipped (either --skip-llm was set or an earlier step failed).*"

        lines = [
            "## LLM Workflow Extraction",
            "",
            f"- Pattern:    **{self.extraction.pattern}**",
            f"- Confidence: {self.extraction.confidence:.2f}",
            f"- Schema valid: {self.extraction.schema_valid}",
        ]
        if self.extraction.schema_errors:
            lines.append("")
            lines.append("**Schema errors:**")
            for e in self.extraction.schema_errors:
                lines.append(f"- {e}")

        if self.extraction.notes:
            lines.append("")
            lines.append("**Extractor notes:**")
            for n in self.extraction.notes:
                lines.append(f"- {n}")

        lines.append("")
        lines.append("### Raw extraction JSON")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(self.extraction.data, indent=2))
        lines.append("```")
        return "\n".join(lines)

    def _section_todos(self) -> str:
        if not self.assembly or not self.assembly.todo_markers:
            return "## Open TODOs in project.yaml\n\nNone. The extractor produced a complete workflow definition."
        lines = [
            "## Open TODOs in project.yaml",
            "",
            "The assembler inserted `# TODO` comments in `project.yaml` for the "
            "items below. Review and fill them in before running the pipeline.",
            "",
        ]
        for t in self.assembly.todo_markers:
            lines.append(f"- {t}")
        return "\n".join(lines)

    def _section_validation(self) -> str:
        v = self.validation
        if not v.issues:
            return "## Validation\n\nAll checks passed."

        lines = ["## Validation", ""]
        for severity in ("error", "warning", "info"):
            items = [i for i in v.issues if i.severity == severity]
            if not items:
                continue
            lines.append(f"### {severity.capitalize()}s ({len(items)})")
            lines.append("")
            for i in items:
                lines.append(f"- [{i.source}] {i.message}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def _section_next_steps(self) -> str:
        return (
            "## Next Steps\n\n"
            "1. Review every `# TODO` in `project.yaml`.\n"
            "2. Review the raw extraction JSON above to verify the workflow shape.\n"
            "3. Drop a sample input into `samples/input.json`.\n"
            "4. Validate:\n"
            "   ```\n"
            "   python main.py --input samples/input.json --dry-run\n"
            "   ```\n"
            "5. Run the pipeline:\n"
            "   ```\n"
            "   python main.py --input samples/input.json\n"
            "   ```\n"
            "6. Artifacts land in `runs/<project-name>/<timestamp>/`.\n"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _overall_status(self) -> str:
        if self.validation.errors:
            return "**NEEDS REVIEW** -- validation errors present"
        if (self.assembly and self.assembly.todo_markers) or self.validation.warnings:
            return "**COMPLETED WITH TODOs** -- review flagged items"
        return "**READY TO RUN**"
