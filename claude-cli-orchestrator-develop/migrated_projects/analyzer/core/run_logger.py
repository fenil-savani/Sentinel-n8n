"""
run_logger.py -- Per-run structured logger for the WorkflowEngine.

Writes two outputs for every workflow run:

  runs/<name>/<ts>/execution.log    -- Human-readable execution log with timestamps
  stdout                            -- [RUN] prefixed status lines for live visibility

Design
------
RunLogger is created by WorkflowEngine right after the run directory is
created, then threaded to TaskDispatcher.  Each run gets its own instance --
no global state, no shared file handles.

Output separation
-----------------
  [RUN] console lines  -> print() to stdout  (user-facing, clean, no timestamp noise)
  execution.log        -> timestamped text file in run dir  (developer log)
  Python root logger   -> unchanged (continues writing to logs/<ts>.log)

Truncation rules
----------------
  Input payload preview  : first PREVIEW_INPUT_CHARS chars of JSON serialisation
  Output payload preview : first PREVIEW_OUTPUT_CHARS chars of JSON serialisation
  Feedback preview       : first PREVIEW_FEEDBACK_CHARS chars of text
  All other fields       : never truncated
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Truncation limits
# ---------------------------------------------------------------------------

PREVIEW_INPUT_CHARS    = 500    # agent input payload preview (JSON chars)
PREVIEW_OUTPUT_CHARS   = 500    # agent output payload preview (JSON chars)
PREVIEW_FEEDBACK_CHARS = 300    # feedback text preview


# ---------------------------------------------------------------------------
# Visual separator constants
# ---------------------------------------------------------------------------

_SEP_WIDE   = "=" * 78
_SEP_MEDIUM = "-" * 60
_SEP_ITER   = "-" * 50


# ---------------------------------------------------------------------------
# RunLogger
# ---------------------------------------------------------------------------

class RunLogger:
    """
    Per-run logger that writes to execution.log and stdout.

    Usage
    -----
    with RunLogger(run_id, run_dir, workflow, model) as rl:
        rl.run_start(input_payload)
        rl.iteration_start(1, 3)
        rl.agent_start(...)
        rl.agent_complete(...)
        rl.run_complete(...)
    """

    def __init__(
        self,
        run_id:   str,
        run_dir:  Path,
        workflow: str,
        model:    str,
    ) -> None:
        self.run_id   = run_id
        self.run_dir  = run_dir
        self.workflow = workflow
        self.model    = model

        self._log_path = run_dir / "execution.log"

        # Open in append mode so existing content (if any) is not lost
        self._log_fh = self._log_path.open("a", encoding="utf-8")

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "RunLogger":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def close(self) -> None:
        """Flush and close the log file handle."""
        try:
            self._log_fh.flush()
            self._log_fh.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # High-level event methods
    # ------------------------------------------------------------------

    def run_start(self, input_payload: dict[str, Any]) -> None:
        """Called once at the very start of a workflow run."""
        self._blank()
        self._print(_SEP_WIDE)
        self._print(f">> WORKFLOW START  |  {self.workflow}  |  {self.model}")
        self._print(f"  run_id  : {self.run_id}")
        self._print(f"  run_dir : {self.run_dir}")
        self._print(f"  input   : {len(input_payload)} keys -- "
                    f"{', '.join(list(input_payload.keys())[:8])}"
                    + (" ..." if len(input_payload) > 8 else ""))
        self._print(_SEP_WIDE)
        self._blank()


    def iteration_start(self, iteration: int, max_iterations: int) -> None:
        """Called at the start of each pipeline-level iteration."""
        self._blank()
        self._print(f"{'-'*4} Iteration {iteration} / {max_iterations} {'-'*44}")
        self._blank()


    def agent_start(
        self,
        agent:         str,
        iteration:     int,
        attempt:       int,
        input_mode:    str,
        resolved_input: dict[str, Any],
        allowed_tools: list[str],
    ) -> None:
        """Called immediately before an agent subprocess is invoked."""
        retry_tag = f" (attempt {attempt})" if attempt > 1 else ""
        self._print(f">> [{agent}] STARTING{retry_tag}")
        self._print(f"  mode    : {input_mode}  |  payload: {len(resolved_input)} keys")

        if allowed_tools:
            tools_str = ", ".join(allowed_tools)
            # wrap long tool lists for readability
            if len(tools_str) > 70:
                self._print(f"  tools   : {len(allowed_tools)} tools")
                for t in allowed_tools:
                    self._print(f"            - {t}")
            else:
                self._print(f"  tools   : {tools_str}")

        self._print(f"  input   : {self._preview(resolved_input, PREVIEW_INPUT_CHARS)}")
        self._blank()


    def agent_complete(
        self,
        agent:         str,
        iteration:     int,
        attempt:       int,
        success:       bool,
        output:        dict[str, Any],
        cost_usd:      float,
        num_turns:     int,
        elapsed_sec:   float,
        input_tokens:  int = 0,
        output_tokens: int = 0,
        cache_read:    int = 0,
        error:         str = "",
        tool_calls:    list | None = None,
        skills_loaded: list | None = None,
    ) -> None:
        """Called after an agent subprocess completes (success or failure)."""
        if success:
            icon = "[OK]"
            self._print(
                f"{icon} [{agent}] COMPLETE  "
                f"turns={num_turns}  cost=${cost_usd:.4f}  "
                f"in={input_tokens}  out={output_tokens}  cache_r={cache_read}  "
                f"elapsed={elapsed_sec:.1f}s"
            )
            output_keys = list(output.keys()) if output else []
            self._print(f"  output_keys : {output_keys}")
            self._print(f"  output      : {self._preview(output, PREVIEW_OUTPUT_CHARS)}")

            # Skills injected into this agent's system prompt
            if skills_loaded:
                self._print(f"  skills      : {', '.join(skills_loaded)}")
            else:
                self._print("  skills      : (none matched)")

            # Tool calls made during this agent turn
            if tool_calls:
                tool_names = [tc.get("name", "?") for tc in tool_calls]
                self._print(f"  tool_calls  : {len(tool_calls)} calls -- {', '.join(tool_names)}")
                # Product docs loaded = Read calls into product_docs/
                product_docs = [
                    tc.get("input", {}).get("file_path", "")
                    for tc in tool_calls
                    if tc.get("name") == "Read"
                    and "product_docs" in tc.get("input", {}).get("file_path", "")
                ]
                if product_docs:
                    self._print(f"  product_docs: {product_docs}")
            else:
                self._print("  tool_calls  : (none recorded)")
        else:
            icon = "[X]"
            self._print(f"{icon} [{agent}] FAILED  elapsed={elapsed_sec:.1f}s")
            self._print(f"  error : {error[:300]}")

        self._blank()


    def agent_thinking(
        self,
        agent:     str,
        iteration: int,
        attempt:   int,
        thinking:  str,
        run_dir:   Path,
        step_id:   str,
    ) -> None:
        """
        Called after an agent completes when thinking content was captured.

        Writes:
          - A [RUN] summary line to stdout + execution.log
          - A dedicated <step_id>_thinking_iter<N>.md file in the run dir
            for easy human reading during debugging
        """
        chars = len(thinking)
        preview = thinking[:200].replace("\n", " ")
        suffix_extra = f"_attempt{attempt}" if attempt > 1 else ""
        fname = f"{step_id}_thinking_iter{iteration}{suffix_extra}.md"

        self._print(f"  thinking : {chars} chars captured -> {fname}")

        # Write the full thinking content to a dedicated Markdown file
        thinking_path = run_dir / fname
        try:
            thinking_path.write_text(
                f"# Thinking: [{agent}] iter={iteration}"
                + (f" attempt={attempt}" if attempt > 1 else "")
                + f"\n\n{thinking}\n",
                encoding="utf-8",
            )
        except OSError as exc:
            self._print(f"  [!] Could not write thinking file {fname}: {exc}")

    def agent_retry(self, agent: str, attempt: int, max_retries: int) -> None:
        """Called when a step-level retry is about to happen."""
        self._print(f"[~] [{agent}] RETRY  attempt {attempt} / {max_retries}")
        self._blank()

    def exit_condition_result(
        self,
        met:       bool,
        step:      str,
        field:     str,
        value:     str,
        expected:  str,
        iteration: int,
    ) -> None:
        """Called after evaluating the pipeline exit condition."""
        if met:
            self._print(
                f"[OK] EXIT CONDITION MET  "
                f"step={step}  {field}=\"{value}\" == \"{expected}\"  -> PASS"
            )
        else:
            self._print(
                f"[X] EXIT CONDITION  "
                f"step={step}  {field}=\"{value}\" != \"{expected}\"  -> RETRY"
            )
        self._blank()


    def feedback_injected(
        self,
        target:   str,
        source:   str,
        chars:    int,
        preview:  str = "",
    ) -> None:
        """Called when feedback text is injected for the next iteration."""
        self._print(f"** FEEDBACK -> [{target}]  source=[{source}]  {chars} chars")
        if preview:
            trunc = preview[:PREVIEW_FEEDBACK_CHARS]
            suffix = "..." if len(preview) > PREVIEW_FEEDBACK_CHARS else ""
            self._print(f"  preview : {trunc!r}{suffix}")
        self._blank()


    def run_complete(
        self,
        status:       str,
        iterations:   int,
        total_cost:   float,
        total_elapsed: float,
        total_turns:  int = 0,
        input_tokens:  int = 0,
        output_tokens: int = 0,
    ) -> None:
        """Called at the very end of a workflow run."""
        icon  = "[OK]" if status == "PASS" else "[X]"
        label = f"PASS {icon}" if status == "PASS" else f"FAIL {icon}"

        self._print(_SEP_WIDE)
        self._print(
            f">> WORKFLOW COMPLETE  |  {label}  |  "
            f"iterations={iterations}  cost=${total_cost:.4f}  elapsed={total_elapsed:.1f}s"
        )
        self._print(
            f"  tokens  : in={input_tokens}  out={output_tokens}  turns={total_turns}"
        )
        self._print(f"  report  : {self.run_dir / 'execution_report.json'}")
        self._print(_SEP_WIDE)
        self._blank()


    def warn(self, message: str, **data: Any) -> None:
        """Log a warning event."""
        self._print(f"[!] WARNING  {message}")
        self._blank()

    def error(self, message: str, **data: Any) -> None:
        """Log an error event."""
        self._print(f"[X] ERROR  {message}")
        self._blank()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ts(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _print(self, line: str) -> None:
        """Write one [RUN] line to both stdout and execution.log."""
        prefixed = f"[RUN] {line}"
        # stdout
        print(prefixed, flush=True)
        # execution.log (with timestamp prefix)
        try:
            self._log_fh.write(f"[{self._ts()}] {prefixed}\n")
            self._log_fh.flush()
        except Exception:
            pass

    def _blank(self) -> None:
        """Write a blank line to stdout and execution.log."""
        print("[RUN]", flush=True)
        try:
            self._log_fh.write(f"[{self._ts()}] [RUN]\n")
            self._log_fh.flush()
        except Exception:
            pass

    @staticmethod
    def _preview(obj: Any, max_chars: int) -> str:
        """
        Return a truncated JSON preview of obj.
        Falls back to str() if serialisation fails.
        """
        try:
            s = json.dumps(obj, ensure_ascii=False, default=str)
        except Exception:
            s = str(obj)
        if len(s) > max_chars:
            return s[:max_chars] + "..."
        return s
