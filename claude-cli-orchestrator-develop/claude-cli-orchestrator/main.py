#!/usr/bin/env python3
"""
main.py -- Entry point for the claude-cli-orchestrator pipeline.

Usage
-----
# Run with a JSON file as input:
  python main.py --input samples/test_input.json

# Run with an inline JSON string:
  python main.py --json '{"source_platform": "splunk", ...}'

# Run with verbose logging:
  python main.py --input samples/test_input.json --verbose

# Override the model:
  python main.py --input samples/test_input.json --model claude-sonnet-4-6

# Dry-run: validate config + input without invoking Claude:
  python main.py --input samples/test_input.json --dry-run

Output
------
- output.json written to runs/query-generator/<timestamp>/output.json
- All intermediate files (generator_iter1.json, reviewer_iter1.json, etc.)
  are written to the same run directory for traceability.
- Final summary printed to stdout.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path bootstrap
# Must be done BEFORE importing local modules so Python finds them.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import (
    AGENT_TIMEOUT,
    CLAUDE_BIN,
    DEFAULT_MODEL,
    KNOWN_MODELS,
    LOG_LEVEL,
    MCP_CONFIG_PATH,
    PROJECT_CONFIG_PATH,
    RUNS_DIR,
)
from core.cli_client import ClaudeCliClient
from core.orchestrator import WorkflowEngine
from observability.reporter import print_summary


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(verbose: bool) -> None:
    """Configure root logger with a UTF-8 console handler (stdout)."""
    level = logging.DEBUG if verbose else getattr(logging, LOG_LEVEL, logging.INFO)
    fmt   = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    formatter = logging.Formatter(fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    if hasattr(console_handler.stream, "reconfigure"):
        console_handler.stream.reconfigure(encoding="utf-8")

    logging.basicConfig(level=level, handlers=[console_handler])
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _attach_run_log(run_dir: Path) -> None:
    """Add a file handler writing to runs/<ts>/run.log after the run dir exists."""
    log_file = run_dir / "run.log"
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    try:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter(fmt))
        logging.getLogger().addHandler(fh)
        logger.info("Log file: %s", log_file)
    except OSError as exc:
        print(f"[WARN] Could not open log file {log_file}: {exc}", file=sys.stderr)


logger = logging.getLogger("main")


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------

def _load_input(args: argparse.Namespace) -> dict:
    """Return the input payload dict from --input file or --json string."""
    if args.json:
        try:
            return json.loads(args.json)
        except json.JSONDecodeError as exc:
            logger.error("--json is not valid JSON: %s", exc)
            sys.exit(1)

    if args.input:
        path = Path(args.input)
        if not path.exists():
            logger.error("Input file not found: %s", path)
            sys.exit(1)
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            logger.error("Input file is not valid JSON: %s", exc)
            sys.exit(1)

    logger.error("Provide --input <file> or --json '<json_string>'")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Dry-run validation
# ---------------------------------------------------------------------------

def _dry_run(input_payload: dict) -> None:
    """Validate configuration and input payload without calling Claude."""
    print("\n=== DRY RUN ===")

    # Check claude binary
    if Path(CLAUDE_BIN).exists():
        print(f"  [OK] Claude CLI binary: {CLAUDE_BIN}")
    else:
        print(f"  [FAIL] Claude CLI binary not found: {CLAUDE_BIN}")

    # Check MCP config
    if MCP_CONFIG_PATH.exists():
        print(f"  [OK] MCP config: {MCP_CONFIG_PATH}")
        mcp = json.loads(MCP_CONFIG_PATH.read_text())
        for server, cfg in mcp.get("mcpServers", {}).items():
            print(f"       -> {server}: {cfg.get('url')}")
    else:
        print(f"  [WARN] MCP config not found: {MCP_CONFIG_PATH}")

    # Check project.yaml + agent files
    if PROJECT_CONFIG_PATH.exists():
        print(f"  [OK] project.yaml: {PROJECT_CONFIG_PATH}")
        try:
            from core.workflow_loader import load_workflow
            wf = load_workflow(PROJECT_CONFIG_PATH)
            for sa in wf.subagents.values():
                from pathlib import Path as _Path
                from config.settings import PROJECT_ROOT, AGENTS_DIR
                pf = _Path(sa.prompt_file)
                md_path = (PROJECT_ROOT / pf) if (pf.parts[0] in ("agents",) or "/" in sa.prompt_file) else (AGENTS_DIR / pf)
                status  = "OK" if md_path.exists() else "FAIL"
                print(f"  [{status}] Agent '{sa.name}': {md_path}")
        except Exception as exc:
            print(f"  [FAIL] Could not load project.yaml: {exc}")
    else:
        print(f"  [FAIL] project.yaml not found: {PROJECT_CONFIG_PATH}")

    # Check product_docs
    from config.settings import PRODUCT_DOCS_DIR
    dest_platform = input_payload.get("destination_platform", "")
    if dest_platform:
        pc_path = PRODUCT_DOCS_DIR / dest_platform / "platform_config.md"
        status  = "OK" if pc_path.exists() else "WARN (missing)"
        print(f"  [{status}] platform_config: {pc_path}")

    # Show input summary
    print("\n  Input payload summary:")
    for k in ["source_platform", "destination_platform", "migration_type",
              "summary", "intent", "use_case"]:
        v = input_payload.get(k, "<missing>")
        print(f"    {k}: {str(v)[:80]}")

    field_count = len(input_payload.get("field_mappings", []))
    print(f"    field_mappings: {field_count} entries")

    print("\n=== DRY RUN COMPLETE -- no Claude calls made ===\n")



# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run the query-generator pipeline using Claude CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source = p.add_mutually_exclusive_group()
    source.add_argument(
        "--input", "-i",
        metavar="FILE",
        help="Path to JSON input file.",
    )
    source.add_argument(
        "--json", "-j",
        metavar="JSON_STRING",
        help="Input payload as an inline JSON string.",
    )

    p.add_argument(
        "--model", "-m",
        default=DEFAULT_MODEL,
        help=f"Claude model to use (default: {DEFAULT_MODEL}).",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and input without invoking Claude.",
    )
    p.add_argument(
        "--no-skills",
        action="store_true",
        help="Disable skill injection (useful for debugging).",
    )
    p.add_argument(
        "--output", "-o",
        metavar="FILE",
        help="Also write the final output JSON to this file path.",
    )
    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = _build_parser()
    args   = parser.parse_args()

    _setup_logging(args.verbose)
    logger.debug("Claude CLI binary: %s", CLAUDE_BIN)

    # Warn on unknown models -- Claude CLI will validate, but this catches typos early
    if args.model not in KNOWN_MODELS:
        logger.warning(
            "Unknown model '%s'. Known models: %s. Proceeding -- Claude CLI will validate.",
            args.model,
            ", ".join(sorted(KNOWN_MODELS)),
        )

    # Load input
    input_payload = _load_input(args)

    # Dry-run mode
    if args.dry_run:
        _dry_run(input_payload)
        return 0

    # Build client with chosen model
    client = ClaudeCliClient(
        model=args.model,
        timeout=AGENT_TIMEOUT,
        verbose=args.verbose,
    )

    # Run the workflow engine (reads project.yaml, executes agents)
    engine = WorkflowEngine(
        client=client,
        project_yaml=PROJECT_CONFIG_PATH,
        load_skills=not args.no_skills,
    )
    _attach_run_log(engine.run_dir)

    output = engine.run(input_payload)

    # Optionally write to a caller-specified output path
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(output, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        logger.info("Output also written to: %s", out_path)

    # Print execution summary (cost + trace + generated output)
    from core.execution_trace import ExecutionTrace
    report_path = engine.run_dir / "execution_report.json"
    if report_path.exists():
        import json as _json
        try:
            rdata   = _json.loads(report_path.read_text(encoding="utf-8-sig"))
            # Re-hydrate a lightweight trace just for printing
            from core.execution_trace import StepTrace, TraceEvent
            lite_trace = ExecutionTrace(
                run_id=rdata["run_id"],
                workflow=rdata["workflow"],
                model=rdata["model"],
                started_at=rdata.get("started_at", ""),
                completed_at=rdata.get("completed_at", ""),
                final_status=rdata["final_status"],
                total_iterations=rdata.get("total_iterations", 0),
            )
            for s in rdata.get("steps", []):
                tok = s.get("tokens") or {}
                lite_trace.record_step(StepTrace(
                    step_id=s["step_id"],
                    iteration=s["iteration"],
                    agent_name=s["agent"],
                    success=s["success"],
                    cost_usd=s.get("cost_usd", 0.0),
                    num_turns=s.get("num_turns", 0),
                    elapsed_sec=s.get("elapsed_sec", 0.0),
                    timestamp=s.get("timestamp", ""),
                    prompt_preview=s.get("prompt_preview", ""),
                    output_keys=s.get("output_keys", []),
                    error=s.get("error") or "",
                    input_tokens=tok.get("input", 0),
                    output_tokens=tok.get("output", 0),
                    cache_read_input_tokens=tok.get("cache_read", 0),
                    cache_creation_input_tokens=tok.get("cache_creation", 0),
                    duration_ms=s.get("duration_ms", 0),
                ))
            print_summary(lite_trace, engine.run_dir, output)
        except Exception as exc:
            logger.warning("Could not print summary: %s", exc)

    # Exit code: 0 for PASS, 1 for FAIL
    return 0 if output.get("status") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
