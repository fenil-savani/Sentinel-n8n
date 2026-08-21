"""Plumbing shared by every single-stage generator module (parser, analytic
rule, TDD, and future single-stage kinds) — the parts that are identical
regardless of artifact shape: pausing on `request_input`, failing when the
model never calls its submit tool, and building the revision-flavored user
prompt. Workbook's two-stage manifest/panel pipeline doesn't fit this shape
and is left alone.
"""

from __future__ import annotations

from typing import Any

from ..llm.base import RunResult


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
