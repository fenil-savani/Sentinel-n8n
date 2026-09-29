"""
execution_trace.py -- Dataclasses for recording a full workflow run.

ExecutionTrace is built up incrementally by TaskDispatcher during the run
and serialized to execution_report.json by observability/reporter.py at the end.

Design rules
------------
- Never truncate data here. Truncation (prompt_preview to 200 chars) happens
  at the point of capture in TaskDispatcher._get_prompt_preview().
- All fields must be JSON-serializable (str, int, float, bool, list, dict, None).
- Properties (total_cost_usd etc.) are computed at read time -- no cached state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# StepTrace -- one agent invocation
# ---------------------------------------------------------------------------

@dataclass
class StepTrace:
    """Record of a single agent invocation (one step, one attempt)."""
    step_id:        str
    iteration:      int           # pipeline iteration (1-based) or step attempt
    agent_name:     str
    success:        bool
    cost_usd:       float
    num_turns:      int
    elapsed_sec:    float
    timestamp:      str           # ISO-8601 UTC
    prompt_preview: str           # First 200 chars of system prompt -- TRUNCATED at capture
    output_keys:    list[str]     # Top-level keys returned by the agent
    error:          str  = ""     # Non-empty only when success=False
    # Token counts (from Claude CLI envelope.usage)
    input_tokens:                int = 0
    output_tokens:               int = 0
    cache_read_input_tokens:     int = 0
    cache_creation_input_tokens: int = 0
    duration_ms:                 int = 0
    # Detailed execution trace
    tool_calls:    list = field(default_factory=list)   # [{"name": str, "input": dict}, ...]
    skills_loaded: list = field(default_factory=list)   # human-readable skill names injected


# ---------------------------------------------------------------------------
# TraceEvent -- a timestamped event in the execution timeline
# ---------------------------------------------------------------------------

@dataclass
class TraceEvent:
    """One event in the workflow execution timeline."""
    event:     str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ExecutionTrace -- full run record
# ---------------------------------------------------------------------------

@dataclass
class ExecutionTrace:
    """
    Complete trace of one workflow run.

    Built up by TaskDispatcher, finalized by WorkflowEngine, then passed to
    observability/reporter.py to produce execution_report.json and the
    printed summary.
    """
    run_id:       str
    workflow:     str
    model:        str
    started_at:   str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at:     str = ""
    final_status:     str = "UNKNOWN"
    total_iterations: int = 0

    steps:  list[StepTrace]  = field(default_factory=list)
    events: list[TraceEvent] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def record_step(self, step: StepTrace) -> None:
        self.steps.append(step)

    def record_event(self, event: str, **data: Any) -> None:
        self.events.append(TraceEvent(event=event, data=data))

    def complete(self, status: str, iterations: int) -> None:
        self.final_status     = status
        self.total_iterations = iterations
        self.completed_at     = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Computed aggregates (read-only properties)
    # ------------------------------------------------------------------

    @property
    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.steps)

    @property
    def total_turns(self) -> int:
        return sum(s.num_turns for s in self.steps)

    @property
    def total_elapsed_sec(self) -> float:
        return sum(s.elapsed_sec for s in self.steps)

    @property
    def cost_by_step(self) -> dict[str, float]:
        """Aggregate cost per step_id across all iterations."""
        totals: dict[str, float] = {}
        for s in self.steps:
            totals[s.step_id] = totals.get(s.step_id, 0.0) + s.cost_usd
        return totals

    @property
    def turns_by_step(self) -> dict[str, int]:
        """Aggregate turn count per step_id across all iterations."""
        totals: dict[str, int] = {}
        for s in self.steps:
            totals[s.step_id] = totals.get(s.step_id, 0) + s.num_turns
        return totals

    @property
    def total_input_tokens(self) -> int:
        return sum(s.input_tokens for s in self.steps)

    @property
    def total_output_tokens(self) -> int:
        return sum(s.output_tokens for s in self.steps)

    @property
    def total_cache_read_tokens(self) -> int:
        return sum(s.cache_read_input_tokens for s in self.steps)

    @property
    def total_cache_creation_tokens(self) -> int:
        return sum(s.cache_creation_input_tokens for s in self.steps)
