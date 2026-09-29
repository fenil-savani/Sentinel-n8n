"""
orchestrator.py -- WorkflowEngine: generic multi-agent execution engine.

Reads project.yaml to discover agents and the workflow definition.
Delegates step execution to TaskDispatcher (retry logic, template resolution,
feedback injection, tracing).  Assembles the final output.json and writes
execution_report.json via observability/reporter.py.

This module replaces the hardcoded QueryGeneratorOrchestrator. All
project-specific logic now lives in project.yaml -- no Python changes are
needed to onboard a new project.

Usage
-----
    engine = WorkflowEngine(client=client)
    output = engine.run(input_payload)
    # output["status"]            -> "PASS" or "FAIL"
    # output["destination_query"] -> generated query (query-generator)
    # engine.run_dir              -> Path to this run's directory
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import (
    AGENT_TIMEOUT,
    MCP_CONFIG_PATH,
    PROJECT_CONFIG_PATH,
    PROJECT_ROOT,
    RUNS_DIR,
    SKILLS_DIR,
)
from core.agent_runner import AgentRunner
from core.cli_client import ClaudeCliClient
from core.execution_trace import ExecutionTrace
from core.run_logger import RunLogger
from core.task_dispatcher import TaskDispatcher
from core.workflow_loader import WorkflowDef, load_workflow

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output assembly
# ---------------------------------------------------------------------------

def _assemble_output(
    input_payload: dict[str, Any],
    workflow:      WorkflowDef,
    context,       # WorkflowContext (imported below to avoid circular)
    final_status:  str,
) -> dict[str, Any]:
    """
    Build the final output dict that callers receive.

    Strategy:
    1. Copy passthrough_fields from the original input unchanged.
    2. Merge ALL step outputs in topological order (earlier steps first, later
       steps override on key conflicts).  This ensures that a generator step's
       'result' field is not lost when a downstream reviewer step is the leaf.
    3. Add pipeline-level metadata (status, confidence_score).
    """
    output: dict[str, Any] = {
        k: input_payload.get(k)
        for k in workflow.passthrough_fields
        if k in input_payload
    }

    # Merge every step's output in declaration order (topological -- producer
    # before consumer).  Later steps override duplicate keys intentionally.
    for step in workflow.steps:
        step_out = context.get_output(step.id)
        if step_out:
            output.update(step_out)

    output["status"]           = final_status
    output["confidence_score"] = 1.0 if final_status == "PASS" else 0.0

    return output


# ---------------------------------------------------------------------------
# Run directory management
# ---------------------------------------------------------------------------

def _create_run_dir(workflow_name: str) -> Path:
    ts  = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run = RUNS_DIR / workflow_name / ts
    run.mkdir(parents=True, exist_ok=True)
    logger.info("Run directory: %s", run)
    return run


# ---------------------------------------------------------------------------
# WorkflowEngine
# ---------------------------------------------------------------------------

class WorkflowEngine:
    """
    Generic multi-agent workflow execution engine.

    Reads project.yaml to determine which agents to run, in what order,
    with what inputs, and how to handle retries and feedback.

    Parameters
    ----------
    client       : ClaudeCliClient -- model, timeout, verbose flag.
                   Defaults to a new client with settings from config/settings.py.
    project_yaml : Path to project.yaml.
                   Defaults to PROJECT_CONFIG_PATH (PROJECT_ROOT/project.yaml).
    load_skills  : Whether to inject SKILL.md context into agent system prompts.
    run_dir      : Override the run directory (useful in tests).
    """

    def __init__(
        self,
        client:       ClaudeCliClient | None = None,
        project_yaml: Path | None = None,
        load_skills:  bool = True,
        run_dir:      Path | None = None,
    ) -> None:
        yaml_path     = project_yaml or PROJECT_CONFIG_PATH
        self.workflow: WorkflowDef = load_workflow(yaml_path)

        # Build agent_definitions from project.yaml subagents (overrides settings.py)
        agent_defs = {
            name: {
                "prompt_file": sa.prompt_file,
                "tools":       sa.tools,
            }
            for name, sa in self.workflow.subagents.items()
        }

        self.runner = AgentRunner(
            client=client or ClaudeCliClient(timeout=AGENT_TIMEOUT),
            load_skills=load_skills,
            agent_definitions=agent_defs,
        )
        self.run_dir: Path = run_dir or _create_run_dir(self.workflow.name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        """
        Execute the full workflow and return the assembled output dict.

        Always writes:
          <run_dir>/input.json           -- raw input payload
          <run_dir>/<step>_iter<N>.json  -- intermediate step files
          <run_dir>/output.json          -- final assembled output
          <run_dir>/execution_report.json -- full observability report

        Returns the same dict that is written to output.json.
        """
        logger.info(
            "=== WorkflowEngine START | workflow=%s | model=%s ===",
            self.workflow.name,
            self.runner.client.model,
        )
        t_start = time.monotonic()

        # Persist raw input for traceability
        self._write_json(self.run_dir / "input.json", input_payload)

        # Build execution trace (accumulated throughout the run)
        trace = ExecutionTrace(
            run_id=self.run_dir.name,
            workflow=self.workflow.name,
            model=self.runner.client.model,
        )

        # Per-run structured logger -- writes execution.log + [RUN] stdout
        run_logger = RunLogger(
            run_id=self.run_dir.name,
            run_dir=self.run_dir,
            workflow=self.workflow.name,
            model=self.runner.client.model,
        )
        run_logger.run_start(input_payload)

        # Execute
        dispatcher = TaskDispatcher(
            runner=self.runner,
            workflow=self.workflow,
            trace=trace,
            run_logger=run_logger,
        )
        context, final_status = dispatcher.run_pipeline(input_payload, self.run_dir)

        elapsed = time.monotonic() - t_start
        trace.complete(final_status, context.current_iteration)

        logger.info(
            "=== WorkflowEngine DONE | status=%s | iterations=%d | elapsed=%.1fs ===",
            final_status,
            context.current_iteration,
            elapsed,
        )

        # Log workflow completion summary
        run_logger.run_complete(
            status=final_status,
            iterations=context.current_iteration,
            total_cost=trace.total_cost_usd,
            total_elapsed=elapsed,
            total_turns=trace.total_turns,
            input_tokens=trace.total_input_tokens,
            output_tokens=trace.total_output_tokens,
        )
        run_logger.close()

        # Assemble and persist final output
        final_output = _assemble_output(input_payload, self.workflow, context, final_status)
        self._write_json(self.run_dir / "output.json", final_output)
        logger.info("output.json written: %s", self.run_dir / "output.json")

        # Write execution_report.json
        from observability.reporter import write_execution_report
        write_execution_report(self.run_dir, trace)

        return final_output

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        try:
            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except (OSError, TypeError) as exc:
            logger.warning("Could not write %s: %s", path, exc)
