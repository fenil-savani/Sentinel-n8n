"""Plumbing shared by every single-stage generator module (parser, analytic
rule, TDD, and future single-stage kinds) — the parts that are identical
regardless of artifact shape: pausing on `request_input`, failing when the
model never calls its submit tool, and building the revision-flavored user
prompt. Workbook's two-stage manifest/panel pipeline doesn't fit this shape
and is left alone.
"""

from __future__ import annotations

import logging
from typing import Any

from ..llm.anthropic import AnthropicRuntime
from ..llm.base import AgentRuntime, RunResult, UsageCallback
from ..llm.claude_cli import ClaudeCliRuntime
from ..llm.pricing import Usage
from ..store import Store

log = logging.getLogger(__name__)


def handle_pause_or_failure(
    draft_id: str, result: RunResult, submit_tool: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Returns `(response, validation)` if the run paused or ended without
    submitting, else `None` to tell the caller to keep processing
    `result.payload`. The caller is responsible for persisting `validation`
    and returning `response` as-is.
    """
    if result.terminal_tool == "request_input":
        payload = result.payload or {}
        return (
            {
                "draft_id": draft_id,
                "status": "needs_input",
                "missing": payload.get("missing", []),
                "question": payload.get("question", ""),
            },
            {"needs_input": payload, "tool_trace": result.tool_trace},
        )
    if result.terminal_tool != submit_tool:
        return (
            {
                "draft_id": draft_id,
                "status": "failed",
                "error": f"The model finished without calling {submit_tool}.",
            },
            {
                "error": "no_submission",
                "message": f"the model stopped without calling {submit_tool}",
                "text": result.text[:2000],
                "tool_trace": result.tool_trace,
            },
        )
    return None


def provider_name(runtime: AgentRuntime) -> str:
    """Maps a runtime instance to the `llm_usage.provider` CHECK-constrained
    value. isinstance-based rather than trusting `settings.llm_provider`
    directly — build_runtime() silently falls back to AnthropicRuntime on an
    unrecognized config value, and a mismatched provider string would fail
    the DB constraint and (via usage_recorder's swallowed exception) just
    silently drop that row rather than crash, which is the right degrade but
    worth getting right at the source instead."""
    if isinstance(runtime, ClaudeCliRuntime):
        return "claude_cli"
    if isinstance(runtime, AnthropicRuntime):
        return "anthropic"
    return "anthropic"  # unknown future runtime: conservative fallback


def usage_recorder(
    *, store: Store, run_id: str, draft_id: str | None, session_id: str | None,
    provider: str, call_site: str,
) -> UsageCallback:
    """Builds an `on_call` callback for `AgentRuntime.run()` that persists one
    `llm_usage` row per underlying LLM call. A usage-tracking failure (e.g. a
    transient DB hiccup) must never break the generation itself — logged and
    swallowed rather than raised."""

    async def on_call(usage: Usage, model: str, iteration: int, latency_ms: int | None) -> None:
        try:
            await store.record_llm_usage(
                run_id=run_id, draft_id=draft_id, session_id=session_id,
                provider=provider, model=model, call_site=call_site,
                iteration=iteration, usage=usage, latency_ms=latency_ms,
            )
        except Exception as exc:  # noqa: BLE001 - deliberate: never break generation over this
            log.warning("run %s: failed to record llm usage: %s", run_id, exc)

    return on_call


async def usage_summary(store: Store, run_id: str) -> dict[str, Any]:
    """The `usage` key added to every /generate/* and /revise/* response.
    Queried back from Postgres rather than accumulated in Python so it
    matches what's actually durable, even on a partially-failed run."""
    totals = await store.usage_totals_for_run(run_id)
    total_cost = totals["total_cost_usd"]
    return {
        "run_id": totals["run_id"],
        "calls": totals["calls"],
        "total_input_tokens": totals["total_input_tokens"],
        "total_output_tokens": totals["total_output_tokens"],
        "total_cache_read_tokens": totals["total_cache_read_tokens"],
        "total_cache_write_tokens": totals["total_cache_write_tokens"],
        "total_cost_usd": float(total_cost) if total_cost is not None else None,
    }


def revision_prompt(*, kind_label: str, current_artifact: str, feedback: str, submit_tool: str) -> str:
    """The user prompt for a revision run: same submit contract as a fresh
    generation, but pointed at an existing artifact plus the analyst's
    requested change instead of intake fields."""
    return (
        f"Revise this existing {kind_label}. Apply ONLY the analyst's requested change below — "
        "keep everything else about the existing artifact intact unless the change requires "
        "otherwise.\n\n"
        f"Current artifact:\n```\n{current_artifact}\n```\n\n"
        f"Analyst's requested change: {feedback}\n\n"
        f"Submit the COMPLETE revised artifact with {submit_tool} — the whole thing, not a diff."
    )
