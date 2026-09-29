"""
migrate.py -- Orchestrator of the 7-step migration pipeline.

Public entry point:
    python -m tools.migrate <agentweave_project_path> --dest <output_dir>

Stages:
    1. Validate source
    2. Scaffold destination (engine bundle + project assets)
    3. Classify pattern (heuristic)
    4. LLM extract workflow  (skipped with --skip-llm)
    5. Assemble project.yaml
    6. Validate generated project
    7. Write migration_report.md
"""

from __future__ import annotations

import argparse
import logging
import sys
import yaml
from dataclasses import dataclass
from pathlib import Path

from tools.migrate.assembler import Assembler, AssemblyResult, AssemblerError
from tools.migrate.classifier import Classifier, ClassificationResult
from tools.migrate.extractor import Extractor, ExtractionResult, ExtractorError
from tools.migrate.reporter import Reporter
from tools.migrate.scaffolder import Scaffolder, ScaffoldError
from tools.migrate.validator import Validator, ValidationReport


logger = logging.getLogger("tools.migrate")


# ---------------------------------------------------------------------------
# Engine root resolution
# ---------------------------------------------------------------------------

def _engine_root() -> Path:
    """
    This file lives at <orchestrator_root>/tools/migrate/migrate.py.
    So engine_root = parents[2].
    """
    return Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# CLI args
# ---------------------------------------------------------------------------

@dataclass
class MigrateArgs:
    source:   Path
    dest:     Path
    model:    str
    force:    bool
    verbose:  bool
    skip_llm: bool


def _parse_args(argv: list[str] | None = None) -> MigrateArgs:
    p = argparse.ArgumentParser(
        prog="python -m tools.migrate",
        description="Migrate an AgentWeave project into a standalone claude-cli-orchestrator project.",
    )
    p.add_argument("source", type=Path,
                   help="Path to the AgentWeave project (the directory containing project.yaml)")
    p.add_argument("--dest", type=Path, required=True,
                   help="Where to create the migrated project (must not exist unless --force)")
    p.add_argument("--model", default="claude-sonnet-4-6",
                   help="Model for the LLM extractor (default: claude-sonnet-4-6)")
    p.add_argument("--force", action="store_true",
                   help="Overwrite dest if it already exists")
    p.add_argument("--verbose", action="store_true",
                   help="Verbose logging")
    p.add_argument("--skip-llm", action="store_true",
                   help="Skip the LLM extractor step (writes a stub project.yaml with TODOs)")

    ns = p.parse_args(argv)
    return MigrateArgs(
        source=ns.source,
        dest=ns.dest,
        model=ns.model,
        force=ns.force,
        verbose=ns.verbose,
        skip_llm=ns.skip_llm,
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run(args: MigrateArgs) -> int:
    _setup_logging(args.verbose)
    logger.info("===============================================================")
    logger.info(" Migration pipeline starting")
    logger.info("   source : %s", args.source)
    logger.info("   dest   : %s", args.dest)
    logger.info("   model  : %s", args.model)
    logger.info("   skip_llm: %s", args.skip_llm)
    logger.info("===============================================================")

    # ---- Step 1 + 2: validate source & scaffold destination ----
    try:
        scaffolder = Scaffolder(
            source=args.source,
            dest=args.dest,
            engine_root=_engine_root(),
            force=args.force,
        )
        scaffold_report = scaffolder.run()
    except ScaffoldError as e:
        logger.error("Scaffold failed: %s", e)
        return 2

    logger.info("[1/7] Source validated + destination scaffolded.")
    logger.info("       Agents copied: %s", scaffold_report.agents_copied)

    # ---- Step 3: pattern classification ----
    num_subagents = _count_subagents(scaffold_report.source_yaml_path)
    scaffold_report.subagent_defs = _load_subagent_defs(scaffold_report.source_yaml_path)
    scaffold_report.project_name  = _load_project_name(scaffold_report.source_yaml_path)

    classification = Classifier().classify(
        supervisor_md=scaffold_report.supervisor_path,
        num_subagents=num_subagents,
    )
    logger.info("[2/7] Heuristic pattern=%s confidence=%.2f",
                classification.pattern, classification.confidence)

    # ---- Step 4: LLM extraction ----
    extraction: ExtractionResult | None = None
    if args.skip_llm:
        logger.warning("[3/7] Skipping LLM extractor (--skip-llm).")
        extraction = _stub_extraction(classification, scaffold_report)
    else:
        try:
            agent_paths = _agent_paths_for_extractor(scaffold_report)
            extractor = Extractor(model=args.model, verbose=args.verbose)
            extraction = extractor.extract(
                supervisor_md_path=scaffold_report.supervisor_path,
                source_yaml_path=scaffold_report.source_yaml_path,
                agent_paths=agent_paths,
                classification_hint=classification,
            )
            logger.info("[3/7] LLM extraction done: pattern=%s confidence=%.2f cost=$%.4f",
                        extraction.pattern, extraction.confidence, extraction.cost_usd)
        except ExtractorError as e:
            logger.error("Extractor failed: %s", e)
            # Fall back to a stub so we still produce a partial project + report.
            extraction = _stub_extraction(classification, scaffold_report, error=str(e))

    # ---- Step 5: assemble project.yaml ----
    assembly: AssemblyResult | None = None
    try:
        assembler = Assembler(extraction, scaffold_report.source_yaml_path)
        assembly = assembler.assemble()
        (args.dest / "project.yaml").write_text(assembly.yaml_text, encoding="utf-8")
        logger.info("[4/7] Wrote project.yaml (%d TODO markers).", len(assembly.todo_markers))
    except AssemblerError as e:
        logger.error("Assembler failed: %s", e)

    # ---- Step 6: validate ----
    validator = Validator(dest=args.dest)
    validation: ValidationReport = validator.run(
        extraction_schema_errors=extraction.schema_errors if extraction else [],
        extraction_data=extraction.data if extraction else {},
    )
    logger.info("[5/7] Validation: %d errors, %d warnings",
                len(validation.errors), len(validation.warnings))

    # ---- Step 7: report ----
    reporter = Reporter(
        dest=args.dest,
        scaffold=scaffold_report,
        classification=classification,
        extraction=extraction,
        assembly=assembly,
        validation=validation,
    )
    report_path = reporter.write()
    logger.info("[6/7] Wrote migration_report.md at %s", report_path)
    logger.info("[7/7] Migration complete. Open %s for a full summary.", report_path)

    # Exit code: 0 if clean, 1 if TODOs/warnings, 2 if errors.
    if validation.errors:
        return 2
    if (assembly and assembly.todo_markers) or validation.warnings:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="[migrate] %(message)s",
    )


def _count_subagents(yaml_path: Path) -> int:
    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return len(data.get("subagents") or [])


def _load_subagent_defs(yaml_path: Path) -> list[dict]:
    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return list(data.get("subagents") or [])


def _load_project_name(yaml_path: Path) -> str:
    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("name", "") or ""


def _agent_paths_for_extractor(scaffold_report) -> dict[str, Path]:
    """
    Map each subagent name to its .md path in the destination agents/ dir.
    The extractor reads the Output Schema section from each file.
    """
    agents_dir = scaffold_report.dest / "agents"
    out: dict[str, Path] = {}
    for sub in scaffold_report.subagent_defs:
        name      = sub.get("name", "")
        pfile     = sub.get("prompt_file", "") or ""
        basename  = Path(pfile).name if pfile else f"{name}.md"
        candidate = agents_dir / basename
        if candidate.is_file():
            out[name] = candidate
        else:
            # Try fuzzy fallbacks: name-as-filename.
            for variant in (f"{name}.md", f"{name.replace('-', '_')}.md"):
                c = agents_dir / variant
                if c.is_file():
                    out[name] = c
                    break
    return out


def _stub_extraction(
    classification: ClassificationResult,
    scaffold_report,
    error: str | None = None,
):
    """
    Return a minimal ExtractionResult so the pipeline can still produce a
    best-effort project.yaml when the LLM step is skipped or fails.
    """
    from tools.migrate.extractor import ExtractionResult

    subagents = scaffold_report.subagent_defs
    pattern   = classification.pattern if classification.pattern in ("A", "B", "C") else "unknown"

    steps = []
    for i, sub in enumerate(subagents):
        name = sub.get("name", f"step{i+1}")
        step = {
            "id": name.replace("-", "_"),
            "agent": name,
            "depends_on": [subagents[i-1].get("name", "").replace("-", "_")] if i > 0 else [],
            "max_retries":    None,
            "exit_condition": None,
            "feedback_field": None,
            "confidence":     0.3,
        }
        if pattern == "C":
            step["max_retries"] = 3
            step["exit_condition"] = {"field": "status", "equals": "PASS"}
            step["feedback_field"] = "issues"
        steps.append(step)

    data = {
        "pattern": pattern,
        "confidence": classification.confidence * 0.5 if not error else 0.0,
        "max_iterations": 3 if pattern == "B" else None,
        "steps": steps,
        "pipeline_exit_condition":
            {"step": steps[-1]["id"], "field": "overall_status", "equals": "PASS"}
            if pattern == "B" and steps else None,
        "pipeline_feedback":
            {
                "from_step":  steps[-1]["id"],
                "from_field": "feedback_for_generator",
                "to_step":    steps[0]["id"],
                "as":         "ctx_feedback",
                "fallback":   "generic_validation_feedback",
            }
            if pattern == "B" and len(steps) >= 2 else None,
        "passthrough_fields": [],
        "passthrough_confidence": 0.0,
        "notes": (
            [f"LLM extractor failed: {error}"] if error else
            ["Stub extraction (--skip-llm); review every field manually"]
        ),
    }
    return ExtractionResult(
        data=data,
        pattern=data["pattern"],
        confidence=data["confidence"],
        notes=data["notes"],
        schema_valid=True,
        schema_errors=[],
    )


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        logger.error("Interrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
