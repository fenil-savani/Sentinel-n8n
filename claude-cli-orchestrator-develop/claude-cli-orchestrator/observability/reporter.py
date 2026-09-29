"""
reporter.py -- Write execution_report.json and print the run summary.

Called at the end of every WorkflowEngine.run() call.  Replaces cost.json.

Truncation rules (applied ONLY at the boundary where we capture prompt text):
  - prompt_preview in execution_report.json -> first 200 chars (captured by
    TaskDispatcher._get_prompt_preview before being stored in StepTrace)
  - All other fields (agent output, costs, trace events) are NEVER truncated.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.execution_trace import ExecutionTrace

logger = logging.getLogger(__name__)

_W = 90   # print width (wide enough for token columns)


# ---------------------------------------------------------------------------
# Write execution_report.json
# ---------------------------------------------------------------------------

def write_execution_report(run_dir: Path, trace: ExecutionTrace) -> Path:
    """
    Serialize ExecutionTrace to execution_report.json in run_dir.

    Returns the path to the written file.
    """
    report: dict[str, Any] = {
        "run_id":           trace.run_id,
        "workflow":         trace.workflow,
        "model":            trace.model,
        "started_at":       trace.started_at,
        "completed_at":     trace.completed_at,
        "final_status":     trace.final_status,
        "total_iterations": trace.total_iterations,

        "cost": {
            "total_usd": round(trace.total_cost_usd, 6),
            "by_step":   {k: round(v, 6) for k, v in trace.cost_by_step.items()},
        },
        "usage": {
            "total_turns":                 trace.total_turns,
            "total_elapsed_sec":           round(trace.total_elapsed_sec, 2),
            "turns_by_step":               trace.turns_by_step,
            # NOTE: total_input_tokens is only the non-cached portion (often tiny due to
            # prompt caching). True token volume = input + cache_read + cache_creation + output.
            "total_input_tokens":          trace.total_input_tokens,
            "total_output_tokens":         trace.total_output_tokens,
            "total_cache_read_tokens":     trace.total_cache_read_tokens,
            "total_cache_creation_tokens": trace.total_cache_creation_tokens,
            "total_tokens_processed":      (
                trace.total_input_tokens
                + trace.total_output_tokens
                + trace.total_cache_read_tokens
                + trace.total_cache_creation_tokens
            ),
        },

        # Each step invocation (one row per agent call)
        "steps": [
            {
                "step_id":        s.step_id,
                "iteration":      s.iteration,
                "agent":          s.agent_name,
                "success":        s.success,
                "cost_usd":       round(s.cost_usd, 6),
                "num_turns":      s.num_turns,
                "elapsed_sec":    round(s.elapsed_sec, 2),
                "timestamp":      s.timestamp,
                # 200-char truncated system prompt preview (truncated at capture time)
                "prompt_preview": s.prompt_preview,
                "output_keys":    s.output_keys,
                # null when success=True
                "error":          s.error if s.error else None,
                # Token breakdown from Claude CLI envelope.usage
                "tokens": {
                    "input":            s.input_tokens,
                    "output":           s.output_tokens,
                    "cache_read":       s.cache_read_input_tokens,
                    "cache_creation":   s.cache_creation_input_tokens,
                },
                "duration_ms":    s.duration_ms,
                # Detailed observability: skills, tool calls, product docs
                "skills_loaded":     s.skills_loaded,
                "tool_calls":        s.tool_calls,
                "product_docs_loaded": [
                    tc.get("input", {}).get("file_path", "")
                    for tc in s.tool_calls
                    if tc.get("name") == "Read"
                    and "product_docs" in tc.get("input", {}).get("file_path", "")
                ],
            }
            for s in trace.steps
        ],

        # Full execution timeline
        "trace": [
            {"event": e.event, "timestamp": e.timestamp, **e.data}
            for e in trace.events
        ],
    }

    path = run_dir / "execution_report.json"
    try:
        path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        logger.info("Execution report written: %s", path)
    except OSError as exc:
        logger.warning("Could not write execution_report.json: %s", exc)

    return path


# ---------------------------------------------------------------------------
# Print summary to stdout
# ---------------------------------------------------------------------------

def print_summary(
    trace:        ExecutionTrace,
    run_dir:      Path,
    final_output: dict[str, Any] | None = None,
) -> None:
    """
    Print the run summary to stdout after every execution.

    Covers both the /cost view (per-step cost + totals) and the /context
    view (execution trace + last generated output).
    """
    status_label = "PASS [OK]" if trace.final_status == "PASS" else "FAIL [X]"

    print("\n" + "=" * _W)
    print(f"  Workflow:   {trace.workflow:<24} Status: {status_label}")
    print(f"  Model:      {trace.model}")
    print(f"  Report:     {run_dir / 'execution_report.json'}")
    print("=" * _W)

    # --- Execution trace table ---
    if trace.steps:
        print("\n  Execution Trace")
        print("  " + "-" * (_W - 2))
        _print_header()
        for s in trace.steps:
            _print_step_row(s)
        print("  " + "-" * (_W - 2))
        _print_totals(trace)

    print("=" * _W)

    # --- Final output (generated query / result) ---
    if final_output:
        query = (
            final_output.get("destination_query")
            or final_output.get("result")
            or ""
        )
        if query:
            print("\n--- Generated Output ---")
            print(query)
            print("-" * _W)

        warnings = final_output.get("warnings") or []
        if warnings:
            print("\nWarnings:")
            for w in warnings:
                print(f"  * {w}")

    print()


# ---------------------------------------------------------------------------
# Table helpers
# ---------------------------------------------------------------------------

def _print_header() -> None:
    print(
        f"  {'iter':>4}  {'step':<20} {'turns':>5}  {'cost':>9}  "
        f"{'in_tok':>7}  {'out_tok':>7}  {'cache_r':>7}  {'elapsed':>8}  {'ok'}"
    )


def _print_step_row(s) -> None:
    icon     = "[OK]" if s.success else "[X]"
    cost_str = f"${s.cost_usd:.4f}"
    elapsed  = f"{s.elapsed_sec:.1f}s"
    print(
        f"  {s.iteration:>4}  {s.step_id:<20} {s.num_turns:>5}  {cost_str:>9}  "
        f"{s.input_tokens:>7}  {s.output_tokens:>7}  {s.cache_read_input_tokens:>7}  "
        f"{elapsed:>8}  {icon}"
    )


def _print_totals(trace: ExecutionTrace) -> None:
    cost_str    = f"${trace.total_cost_usd:.4f}"
    elapsed_str = f"{trace.total_elapsed_sec:.1f}s"
    print(
        f"  {'':>4}  {'TOTAL':<20} {trace.total_turns:>5}  {cost_str:>9}  "
        f"{trace.total_input_tokens:>7}  {trace.total_output_tokens:>7}  "
        f"{trace.total_cache_read_tokens:>7}  {elapsed_str:>8}"
    )
