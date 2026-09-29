"""
task_dispatcher.py -- Execute workflow steps with template resolution,
retry logic, feedback injection, and execution tracing.

Template expressions (used in project.yaml step `input:` mappings)
-------------------------------------------------------------------
  "$.fieldname"              -- value from input_payload["fieldname"]
  "steps.STEP_ID.fieldname"  -- dot-path into a completed step's output dict
  "ctx.iteration"            -- current iteration number (int, 1-based)
  "ctx.feedback.STEP_ID"     -- feedback string injected for a step
  bare string                -- literal value
  bare dict / list           -- resolved recursively (values may be expressions)
  any other type             -- passed through as a literal

Two execution modes
-------------------
Pipeline-level loop (query-generator):
  WorkflowDef.max_iterations > 1 and exit_condition is set.
  All steps run repeatedly until the exit condition is met or iterations exhausted.
  Feedback is injected between pipeline iterations via FeedbackRules.

Step-level retry (field-mapper):
  StepDef.max_retries > 1 and step.exit_condition is set.
  Individual steps retry independently; the pipeline advances when each step passes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.agent_runner import AgentRunner, AgentResult
from core.execution_trace import ExecutionTrace, StepTrace
from core.run_logger import RunLogger
from core.workflow_loader import WorkflowDef, StepDef, StepCondition, FeedbackRule

logger = logging.getLogger(__name__)

_PROMPT_PREVIEW_CHARS = 200


# ---------------------------------------------------------------------------
# Workflow context -- mutable state across one run
# ---------------------------------------------------------------------------

@dataclass
class WorkflowContext:
    """
    Mutable state threaded through the execution of one workflow run.

    Two co-existing access patterns
    --------------------------------
    Template mode (step has input: block):
        _step_outputs  -- keyed by step_id; accessed via "steps.STEP.field" expressions
        _feedback      -- keyed by step_id; accessed via "ctx.feedback.STEP" expressions

    Auto-chain mode (step has no input: block):
        cumulative_state -- grows with every step's output; passed wholesale to the next
                           step so agents always have the full accumulated context.
    """
    current_iteration: int = 1
    _step_outputs:    dict[str, dict]    = field(default_factory=dict)
    _feedback:        dict[str, str]     = field(default_factory=dict)
    cumulative_state: dict[str, Any]     = field(default_factory=dict)

    def set_output(self, step_id: str, output: dict) -> None:
        self._step_outputs[step_id] = output

    def get_output(self, step_id: str) -> dict:
        return self._step_outputs.get(step_id, {})

    def set_feedback(self, step_id: str, text: str) -> None:
        self._feedback[step_id] = text

    def get_feedback(self, step_id: str) -> str | None:
        return self._feedback.get(step_id)

    def merge_output(self, output: dict) -> None:
        """
        Merge a completed step's output into cumulative_state.

        Later steps (and later iterations) automatically see every field that
        any prior step produced -- without any explicit wiring in project.yaml.
        Key conflicts are resolved last-write-wins (caller order = topo order).
        """
        if isinstance(output, dict):
            self.cumulative_state.update(output)


# ---------------------------------------------------------------------------
# Built-in fallback: generic validation feedback builder
# ---------------------------------------------------------------------------

def generic_validation_feedback(
    context: WorkflowContext,
    source_step_id: str,
    iteration: int,
) -> str:
    """
    Built-in fallback used when a reviewer's feedback_for_generator field is
    null or empty.  Constructs structured feedback from the failed validators
    in the source step's output (any step that returns a validation_results list).

    Works for any agent that follows the standard reviewer output schema:
      validation_results: [{validator, is_valid, explanation, details}, ...]
    """
    output = context.get_output(source_step_id)
    failed = [
        v for v in output.get("validation_results", [])
        if not v.get("is_valid", True)
    ]

    if not failed:
        return ""

    lines = [
        f"=== VALIDATION FEEDBACK (Iteration {iteration}) ===",
        "The previous output had the following validation failures. "
        "You MUST address ALL of them in your next response:",
        "",
    ]

    for v in failed:
        name        = v.get("validator", "UNKNOWN")
        explanation = v.get("explanation", "No explanation provided.")
        details     = v.get("details", {})

        lines.append(f"[{name} -- FAILED]")
        lines.append(explanation)

        for key, value in details.items():
            if isinstance(value, list) and value:
                lines.append(f"  {key}:")
                for item in value:
                    lines.append(f"    - {item}")
            elif value and not isinstance(value, bool):
                lines.append(f"  {key}: {value}")

        lines.append("")

    lines += [
        "Please regenerate the COMPLETE output addressing ALL issues above. "
        "Do not reintroduce previously fixed issues.",
        "=== END FEEDBACK ===",
    ]
    return "\n".join(lines)


_BUILTIN_FALLBACKS: dict[str, Any] = {
    "generic_validation_feedback": generic_validation_feedback,
}


# ---------------------------------------------------------------------------
# Template resolver
# ---------------------------------------------------------------------------

def _resolve_value(expr: Any, payload: dict, context: WorkflowContext) -> Any:
    """
    Recursively resolve a template expression from project.yaml step input.

    Rules:
      dict  -> resolve each value recursively (keys are literal)
      list  -> resolve each element recursively
      str "$.field"              -> payload.get("field")
      str "steps.STEP.field"     -> context._step_outputs[STEP][field] (dot-path)
      str "ctx.iteration"        -> context.current_iteration (int)
      str "ctx.feedback.STEP"    -> context._feedback.get(STEP)
      any other type             -> returned as-is (literal)
    """
    if isinstance(expr, dict):
        return {k: _resolve_value(v, payload, context) for k, v in expr.items()}

    if isinstance(expr, list):
        return [_resolve_value(item, payload, context) for item in expr]

    if not isinstance(expr, str):
        return expr   # int, float, bool, None -- literal

    # Payload field reference
    if expr.startswith("$."):
        return payload.get(expr[2:])

    # Previous step output reference
    if expr.startswith("steps."):
        parts = expr.split(".", 2)      # ["steps", "STEP_ID", "field.path"]
        if len(parts) < 3:
            return None
        _, step_id, field_path = parts
        return _nested_get(context.get_output(step_id), field_path)

    # Context references
    if expr == "ctx.iteration":
        return context.current_iteration

    if expr.startswith("ctx.feedback."):
        return context.get_feedback(expr[len("ctx.feedback."):])

    # Literal string
    return expr


def _nested_get(obj: Any, path: str) -> Any:
    """Traverse a dot-separated path through nested dicts. None on any miss."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


# ---------------------------------------------------------------------------
# Condition evaluator
# ---------------------------------------------------------------------------

def _eval_condition(cond: StepCondition | None, step_output: dict) -> bool:
    """
    Evaluate a condition against a step's output.

    Returns True (condition met) when:
      cond is None                       -> always True (no condition = pass)
      cond.equals is set                 -> str(output[field]) == equals
      cond.not_equals is set             -> str(output[field]) != not_equals
    """
    if cond is None:
        return True

    value    = _nested_get(step_output, cond.field)
    str_val  = str(value) if value is not None else ""

    if cond.equals is not None:
        return str_val == str(cond.equals)

    if cond.not_equals is not None:
        return str_val != str(cond.not_equals)

    return True


# ---------------------------------------------------------------------------
# Task dispatcher
# ---------------------------------------------------------------------------

class TaskDispatcher:
    """
    Executes a WorkflowDef's steps with full template resolution, retry logic,
    feedback injection, and execution tracing.

    Called by WorkflowEngine -- do not call directly from main.py.
    """

    def __init__(
        self,
        runner:     AgentRunner,
        workflow:   WorkflowDef,
        trace:      ExecutionTrace,
        run_logger: RunLogger | None = None,
    ) -> None:
        self.runner     = runner
        self.workflow   = workflow
        self.trace      = trace
        self.run_logger = run_logger

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run_pipeline(
        self,
        payload: dict[str, Any],
        run_dir: Path,
    ) -> tuple[WorkflowContext, str]:
        """
        Execute the full workflow.

        Returns
        -------
        (context, final_status)
          context     -- holds all step outputs accessible via context.get_output()
          final_status -- "PASS" or "FAIL"
        """
        context = WorkflowContext()
        context.cumulative_state = dict(payload)   # seed with full input.json
        wf      = self.workflow

        # Choose execution mode based on workflow config
        if wf.max_iterations > 1 and wf.exit_condition is not None:
            final_status = self._run_pipeline_loop(payload, context, run_dir)
        else:
            self._run_steps_once(payload, context, run_dir, pipeline_iteration=1)
            # Determine status from the last step's exit_condition (or PASS if none)
            last_step = wf.steps[-1]
            last_out  = context.get_output(last_step.id)
            if last_step.exit_condition:
                final_status = (
                    "PASS"
                    if _eval_condition(last_step.exit_condition, last_out)
                    else "FAIL"
                )
            else:
                final_status = "PASS"

        return context, final_status

    # ------------------------------------------------------------------
    # Pipeline-level loop (query-generator pattern)
    # ------------------------------------------------------------------

    def _run_pipeline_loop(
        self,
        payload: dict[str, Any],
        context: WorkflowContext,
        run_dir: Path,
    ) -> str:
        wf           = self.workflow
        final_status = "FAIL"

        for iteration in range(1, wf.max_iterations + 1):
            context.current_iteration = iteration
            self.trace.record_event("iteration_start", iteration=iteration)
            logger.info("--- Pipeline iteration %d / %d ---", iteration, wf.max_iterations)

            if self.run_logger:
                self.run_logger.iteration_start(iteration, wf.max_iterations)

            if not self._run_steps_once(payload, context, run_dir, iteration):
                logger.warning("One or more steps failed on iteration %d", iteration)
                if iteration < wf.max_iterations:
                    continue
                break

            # Evaluate pipeline exit condition
            exit_out       = context.get_output(wf.exit_condition.step)
            condition_met  = _eval_condition(wf.exit_condition, exit_out)
            exit_field_val = _nested_get(exit_out, wf.exit_condition.field)

            if self.run_logger:
                self.run_logger.exit_condition_result(
                    met=condition_met,
                    step=wf.exit_condition.step,
                    field=wf.exit_condition.field,
                    value=str(exit_field_val) if exit_field_val is not None else "",
                    expected=str(wf.exit_condition.equals or ""),
                    iteration=iteration,
                )

            if condition_met:
                final_status = "PASS"
                self.trace.record_event("exit_condition_true", iteration=iteration)
                logger.info("Exit condition MET -- pipeline PASS on iteration %d", iteration)
                break

            # Condition not met
            self.trace.record_event(
                "exit_condition_false",
                iteration=iteration,
                failed_validators=self._extract_failed_validators(exit_out),
            )
            logger.info("Exit condition NOT met on iteration %d", iteration)

            if iteration < wf.max_iterations:
                self._inject_pipeline_feedback(context, iteration)
            else:
                logger.warning(
                    "Max iterations (%d) reached -- returning FAIL", wf.max_iterations
                )

        return final_status

    # ------------------------------------------------------------------
    # Run all steps once (one pass through the DAG)
    # ------------------------------------------------------------------

    def _run_steps_once(
        self,
        payload:            dict[str, Any],
        context:            WorkflowContext,
        run_dir:            Path,
        pipeline_iteration: int,
    ) -> bool:
        """Execute all steps in topological order. Returns True if all succeed."""
        for step in self._topological_order():
            if not self._run_step(step, payload, context, run_dir, pipeline_iteration):
                return False
        return True

    # ------------------------------------------------------------------
    # Single step execution (with optional step-level retry)
    # ------------------------------------------------------------------

    def _run_step(
        self,
        step:               StepDef,
        payload:            dict[str, Any],
        context:            WorkflowContext,
        run_dir:            Path,
        pipeline_iteration: int,
    ) -> bool:
        """
        Run one step, retrying up to step.max_retries times if exit_condition
        is set and not met.  Returns True when step succeeds (condition met).
        """
        step_feedback: str | None = None

        for attempt in range(1, step.max_retries + 1):
            # For step-level retries, ctx.iteration tracks the step attempt.
            if step.max_retries > 1:
                context.current_iteration = attempt

            # ------------------------------------------------------------------
            # Input resolution -- two modes:
            #
            # AUTO-CHAIN (no input: block in project.yaml):
            #   Pass the full cumulative state plus two injected context fields:
            #     ctx_iteration -- which iteration / attempt this is (int)
            #     ctx_feedback  -- feedback text set by a prior FeedbackRule for
            #                     this step (str | None); complements any
            #                     feedback_for_<step> already in cumulative_state
            #   The agent receives everything every prior step produced, with no
            #   explicit wiring required.
            #
            # TEMPLATE MODE (input: block declared):
            #   Existing template-expression resolver -- unchanged.
            #   "$.field", "steps.X.field", "ctx.iteration", "ctx.feedback.X"
            # ------------------------------------------------------------------
            if not step.input:
                resolved_input = dict(context.cumulative_state)
                resolved_input["ctx_iteration"] = context.current_iteration
                feedback = context.get_feedback(step.id)
                if feedback is not None:
                    resolved_input["ctx_feedback"] = feedback
            else:
                resolved_input = _resolve_value(step.input, payload, context) or {}

            # Inject step-level feedback from prior failed attempt (both modes)
            if step_feedback and step.feedback_field:
                resolved_input[step.feedback_field] = step_feedback

            input_mode    = "auto-chain" if not step.input else "template"
            allowed_tools = (
                self.runner.agent_definitions.get(step.agent, {}).get("tools", [])
            )
            self.trace.record_event(
                "step_start",
                step=step.id, iteration=pipeline_iteration, attempt=attempt,
                input_mode=input_mode,
            )
            logger.info(
                "[%s] Starting -- pipeline_iter=%d attempt=%d input_mode=%s payload_keys=%d",
                step.id, pipeline_iteration, attempt, input_mode, len(resolved_input),
            )

            if self.run_logger:
                self.run_logger.agent_start(
                    agent=step.agent,
                    iteration=pipeline_iteration,
                    attempt=attempt,
                    input_mode=input_mode,
                    resolved_input=resolved_input,
                    allowed_tools=allowed_tools,
                )

            result: AgentResult = self.runner.run(step.agent, resolved_input)

            # Write intermediate JSON file
            self._write_step_file(
                run_dir, step, pipeline_iteration, attempt, result, resolved_input
            )

            # Record trace
            self.trace.record_step(StepTrace(
                step_id=step.id,
                iteration=pipeline_iteration,
                agent_name=step.agent,
                success=result.success,
                cost_usd=result.cost_usd,
                num_turns=result.num_turns,
                elapsed_sec=result.elapsed_sec,
                timestamp=datetime.now(timezone.utc).isoformat(),
                prompt_preview=self._get_prompt_preview(step.agent),
                output_keys=list(result.output.keys()) if result.output else [],
                error=result.error,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cache_read_input_tokens=result.cache_read_input_tokens,
                cache_creation_input_tokens=result.cache_creation_input_tokens,
                duration_ms=result.duration_ms,
                tool_calls=result.tool_calls,
                skills_loaded=result.skills_loaded,
            ))

            self.trace.record_event(
                "step_complete",
                step=step.id, iteration=pipeline_iteration, attempt=attempt,
                success=result.success, elapsed_sec=round(result.elapsed_sec, 2),
            )

            if self.run_logger:
                self.run_logger.agent_complete(
                    agent=step.agent,
                    iteration=pipeline_iteration,
                    attempt=attempt,
                    success=result.success,
                    output=result.output or {},
                    cost_usd=result.cost_usd,
                    num_turns=result.num_turns,
                    elapsed_sec=result.elapsed_sec,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    cache_read=result.cache_read_input_tokens,
                    error=result.error or "",
                    tool_calls=result.tool_calls,
                    skills_loaded=result.skills_loaded,
                )
                if result.thinking:
                    self.run_logger.agent_thinking(
                        agent=step.agent,
                        iteration=pipeline_iteration,
                        attempt=attempt,
                        thinking=result.thinking,
                        run_dir=run_dir,
                        step_id=step.id,
                    )

            if not result.success:
                logger.error("[%s] Agent call failed: %s", step.id, result.error)
                if attempt < step.max_retries:
                    logger.info(
                        "[%s] Retrying (attempt %d / %d)", step.id, attempt + 1, step.max_retries
                    )
                    if self.run_logger:
                        self.run_logger.agent_retry(
                            step.agent, attempt + 1, step.max_retries
                        )
                    continue
                return False

            # Store output -- both access patterns updated together:
            #   _step_outputs  -> used by template expressions ("steps.X.field")
            #   cumulative_state -> used by auto-chain (passed wholesale next step)
            context.set_output(step.id, result.output)
            context.merge_output(result.output)

            # Evaluate step-level exit condition (step-level retry pattern)
            if step.exit_condition:
                if _eval_condition(step.exit_condition, result.output):
                    self.trace.record_event(
                        "step_exit_condition_met", step=step.id, attempt=attempt
                    )
                    logger.info("[%s] Step exit condition MET -- step PASS", step.id)
                    return True

                # Condition not met -- extract feedback for next attempt
                if step.feedback_field:
                    step_feedback = result.output.get(step.feedback_field) or ""

                self.trace.record_event(
                    "step_exit_condition_false", step=step.id, attempt=attempt
                )
                if attempt < step.max_retries:
                    continue

                # Exhausted step retries -- log warning but do not block pipeline
                logger.warning(
                    "[%s] Exhausted %d retries without meeting exit condition",
                    step.id, step.max_retries,
                )
                return False

            # No step exit condition -- a clean agent run is a success
            return True

        return False  # exhausted all attempts

    # ------------------------------------------------------------------
    # Pipeline feedback injection
    # ------------------------------------------------------------------

    def _inject_pipeline_feedback(
        self, context: WorkflowContext, iteration: int
    ) -> None:
        """Apply all matching FeedbackRules, injecting text into context."""
        for rule in self.workflow.feedback_rules:
            # Check the 'when' condition against the source step's output
            src_output = context.get_output(rule.when.step)
            if not _eval_condition(rule.when, src_output):
                continue   # condition not triggered

            # Extract feedback text from the declared field
            feedback_text = _nested_get(context.get_output(rule.from_step), rule.from_field)
            feedback_text = feedback_text or ""   # normalise None -> ""

            # Fall back to a built-in builder if the field was empty
            if not feedback_text and rule.fallback:
                fn = _BUILTIN_FALLBACKS.get(rule.fallback)
                if fn:
                    feedback_text = fn(context, rule.from_step, iteration)
                else:
                    logger.warning(
                        "Unknown feedback fallback '%s' -- skipping injection",
                        rule.fallback,
                    )

            if feedback_text:
                context.set_feedback(rule.to_step, feedback_text)
                self.trace.record_event(
                    "feedback_injected",
                    target=rule.to_step,
                    source=rule.from_step,
                    chars=len(feedback_text),
                )
                logger.info(
                    "Feedback injected into '%s' (%d chars)",
                    rule.to_step, len(feedback_text),
                )
                if self.run_logger:
                    self.run_logger.feedback_injected(
                        target=rule.to_step,
                        source=rule.from_step,
                        chars=len(feedback_text),
                        preview=feedback_text,
                    )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _topological_order(self) -> list[StepDef]:
        """Return workflow steps sorted by dependency (Kahn's algorithm)."""
        by_id: dict[str, StepDef] = {s.id: s for s in self.workflow.steps}
        in_deg: dict[str, int]     = {s.id: 0 for s in self.workflow.steps}
        children: dict[str, list[str]] = {s.id: [] for s in self.workflow.steps}

        for s in self.workflow.steps:
            for dep in s.depends_on:
                if dep not in in_deg:
                    raise ValueError(
                        f"Step '{s.id}' depends_on unknown step '{dep}'"
                    )
                in_deg[s.id] += 1
                children[dep].append(s.id)

        queue  = [sid for sid, deg in in_deg.items() if deg == 0]
        result: list[StepDef] = []

        while queue:
            sid = queue.pop(0)
            result.append(by_id[sid])
            for child in children[sid]:
                in_deg[child] -= 1
                if in_deg[child] == 0:
                    queue.append(child)

        if len(result) != len(self.workflow.steps):
            raise ValueError(
                "Circular dependency detected in workflow steps: "
                + str([s.id for s in self.workflow.steps if s not in result])
            )
        return result

    def _write_step_file(
        self,
        run_dir:            Path,
        step:               StepDef,
        pipeline_iteration: int,
        attempt:            int,
        result:             AgentResult,
        resolved_input:     dict,
    ) -> None:
        """Write intermediate JSON file for traceability."""
        # Filename: <step_id>_iter<N>.json (+ _attempt<N> for step-level retries)
        suffix = f"_iter{pipeline_iteration}"
        if step.max_retries > 1:
            suffix += f"_attempt{attempt}"
        path = run_dir / f"{step.id}{suffix}.json"
        try:
            record: dict = {
                "payload":       resolved_input,
                "result":        result.output,
                "raw_text":      result.raw_text,
                "success":       result.success,
                "error":         result.error,
                "skills_loaded": result.skills_loaded,
                "tool_calls":    result.tool_calls,
            }
            if result.thinking:
                record["thinking"] = result.thinking
            path.write_text(
                json.dumps(record, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Could not write step file %s: %s", path, exc)

    def _get_prompt_preview(self, agent_name: str) -> str:
        """
        Return the first 200 chars of the agent's cached system prompt.
        Safe -- never raises, returns empty string on any error.
        """
        try:
            cached = self.runner._base_prompt_cache.get(agent_name, "")
            if len(cached) > _PROMPT_PREVIEW_CHARS:
                return cached[:_PROMPT_PREVIEW_CHARS] + "..."
            return cached
        except Exception:
            return ""

    @staticmethod
    def _extract_failed_validators(output: dict) -> list[str]:
        """Extract the names of failed validators from a reviewer-style output."""
        return [
            v.get("validator", "?")
            for v in output.get("validation_results", [])
            if not v.get("is_valid", True)
        ]
