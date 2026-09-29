"""Parser generation — drives `generate-sentinel-parser.md`.

One agent run, one artifact. The skill does the hard thinking (schema
derivation, `column_ifexists` discipline, normalised field naming); this module
supplies the tools, validates what comes back, and persists it.

Validation order matters. Structure first (does the YAML parse, are the required
keys there), then rules (the lint), then the live query. Running KQL is the
slowest check and the only one that needs Azure, so it goes last — no point
paying for a round trip on a document that was never going to be valid.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from ..azure.logs import LogsClient
from ..config import Settings
from ..lint.rules import lint_parser
from ..llm.base import AgentRuntime, LLMUnavailable, RunResult
from ..output import write_artifact
from ..prompts import parser_system
from ..store import Store, new_id
from ..tools import Toolbox
from ._shared import (
    handle_pause_or_failure,
    provider_name,
    revision_prompt,
    usage_recorder,
    usage_summary,
)

log = logging.getLogger(__name__)


def _snake(text: str) -> str:
    """Lowercase snake_case, per the skill's naming rule — collapses spaces,
    hyphens, and any other punctuation into single underscores. Without this,
    a two-word product/logtype (e.g. "Vectra AI" / "Account Entities") builds
    a fallback alias containing literal spaces, which isn't a valid KQL
    function identifier — the model then correctly refuses and asks for
    clarification instead of guessing, but there's no reason to make it ask
    when the fix is mechanical."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


@dataclass(slots=True)
class ParserRequest:
    product: str
    logtype: str
    table: str
    sample_data: str | None = None
    sample_ref: str | None = None
    reference_parser: str | None = None
    dedup_key: str | None = None
    notes: str | None = None
    solution: str | None = None
    session_id: str | None = None

    def to_prompt(self, available_tools: Iterable[str], reference_dir: Path) -> str:
        # Different runtimes offer different file-access tools (e.g.
        # ClaudeCliRuntime has neither read_reference nor run_python — see
        # its module docstring). read_reference/run_python resolve a bare
        # filename against the reference dir themselves; the CLI's native
        # Read tool does not discover files there by name alone (--add-dir
        # only grants *access*, it doesn't put the dir in Glob's default
        # cwd-scoped search) — so it needs the real absolute path instead.
        names = set(available_tools)
        has_read_reference = "read_reference" in names
        has_run_python = "run_python" in names
        read_hint = "read_reference" if has_read_reference else "the Read tool"
        inspect_hint = "run_python" if has_run_python else "the Read tool"

        lines = [
            "Generate a Microsoft Sentinel parser with these inputs:",
            "",
            f"- Product: {self.product}",
            f"- Log type / category: {self.logtype}",
            f"- Custom table the DCR writes to: {self.table}",
        ]
        if self.dedup_key:
            lines.append(f"- Natural unique key for dedup: {self.dedup_key}")
        if self.reference_parser:
            ref_path = self.reference_parser if has_read_reference \
                else str(reference_dir / self.reference_parser)
            lines.append(
                f"- Reference parser to mirror: {ref_path} "
                f"(read it with {read_hint} BEFORE writing anything)"
            )
        if self.sample_ref:
            sample_path = self.sample_ref if has_run_python \
                else str(reference_dir / self.sample_ref)
            lines.append(
                f"- Sample data file: {sample_path} "
                f"(inspect it with {inspect_hint}; union the keys across EVERY record)"
            )
        if self.notes:
            lines.append(f"- Additional context: {self.notes}")
        if self.sample_data:
            lines += ["", "Sample records:", "```", self.sample_data.strip()[:20_000], "```"]
        lines += [
            "",
            f"The parser's FunctionName and FunctionAlias must both be "
            f"'{_snake(self.product)}_{_snake(self.logtype)}'.",
            "Submit the complete YAML with submit_parser when you are done.",
        ]
        return "\n".join(lines)


async def generate_parser(
    request: ParserRequest,
    *,
    runtime: AgentRuntime,
    settings: Settings,
    store: Store,
    logs: LogsClient | None,
) -> dict[str, Any]:
    alias = f"{_snake(request.product)}_{_snake(request.logtype)}"
    draft_id = await store.create_draft(
        kind="parser", name=alias, session_id=request.session_id
    )
    run_id = new_id("run")
    on_call = usage_recorder(
        store=store, run_id=run_id, draft_id=draft_id, session_id=request.session_id,
        provider=provider_name(runtime), call_site="parser",
    )

    toolbox = Toolbox(settings, logs)
    tools = toolbox.parser_tools()
    available = runtime.supported_tool_names(tools)
    log.info(
        "parser draft %s: starting | provider=%s model=%s product=%s logtype=%s table=%s",
        draft_id, type(runtime).__name__, settings.llm_model_parser,
        request.product, request.logtype, request.table,
    )
    try:
        result = await runtime.run(
            system=parser_system(settings.prompts_dir, available),
            user=request.to_prompt(available, settings.reference_dir),
            tools=tools,
            model=settings.llm_model_parser,
            on_call=on_call,
        )
    except LLMUnavailable as exc:
        # Backend problem, not a generation problem. Drop the placeholder row so
        # it does not look like a failed attempt the analyst needs to review.
        log.warning("parser draft %s: LLM unavailable | provider=%s model=%s error=%s",
                    draft_id, type(runtime).__name__, settings.llm_model_parser, exc)
        await store.update_draft(draft_id, status="failed",
                                 validation={"error": "llm_unavailable"})
        raise

    return await _finish(
        draft_id, run_id=run_id, result=result, table=request.table,
        solution=request.solution or request.product, settings=settings, store=store, logs=logs,
        fallback_alias=alias,
    )


async def revise_parser(
    draft_id: str,
    feedback: str,
    *,
    runtime: AgentRuntime,
    settings: Settings,
    store: Store,
    logs: LogsClient | None,
) -> dict[str, Any]:
    draft = await store.get_draft(draft_id)
    if draft is None or draft["kind"] != "parser":
        return {"draft_id": draft_id, "status": "failed",
                "error": f"no parser draft {draft_id}"}

    summary = draft.get("summary") or {}
    run_id = new_id("run")
    on_call = usage_recorder(
        store=store, run_id=run_id, draft_id=draft_id, session_id=draft.get("session_id"),
        provider=provider_name(runtime), call_site="parser",
    )
    toolbox = Toolbox(settings, logs)
    tools = toolbox.parser_tools()
    available = runtime.supported_tool_names(tools)
    user = revision_prompt(
        kind_label="Microsoft Sentinel parser", current_artifact=draft["artifact"] or "",
        feedback=feedback, submit_tool="submit_parser",
    )
    log.info("parser draft %s: revising | feedback=%s", draft_id, feedback[:200])
    try:
        result = await runtime.run(
            system=parser_system(settings.prompts_dir, available),
            user=user, tools=tools, model=settings.llm_model_parser,
            on_call=on_call,
        )
    except LLMUnavailable as exc:
        log.warning("parser draft %s: LLM unavailable during revision | error=%s", draft_id, exc)
        await store.update_draft(draft_id, status="failed",
                                 validation={"error": "llm_unavailable"})
        raise

    return await _finish(
        draft_id, run_id=run_id, result=result, table=summary.get("table", ""),
        solution=summary.get("solution") or draft["name"], settings=settings, store=store, logs=logs,
        fallback_alias=draft["name"],
    )


async def _finish(
    draft_id: str,
    *,
    run_id: str,
    result: RunResult,
    table: str,
    solution: str,
    settings: Settings,
    store: Store,
    logs: LogsClient | None,
    fallback_alias: str,
) -> dict[str, Any]:
    usage = await usage_summary(store, run_id)

    paused = handle_pause_or_failure(draft_id, result, "submit_parser")
    if paused is not None:
        response, validation = paused
        log.info("parser draft %s: %s", draft_id, response["status"])
        await store.update_draft(draft_id, status=response["status"], validation=validation)
        return {**response, "usage": usage}

    raw = _strip_fences((result.payload or {}).get("yaml", ""))

    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        await store.update_draft(
            draft_id, status="failed", artifact=raw,
            validation={"error": "invalid_yaml", "message": str(exc)[:800]},
        )
        return {"draft_id": draft_id, "status": "failed",
                "error": f"Generated YAML does not parse: {exc}", "usage": usage}

    if not isinstance(doc, dict):
        await store.update_draft(
            draft_id, status="failed", artifact=raw,
            validation={"error": "invalid_yaml", "message": "top level is not a mapping"},
        )
        return {"draft_id": draft_id, "status": "failed",
                "error": "Generated YAML is not a mapping.", "usage": usage}

    lint_ok, findings = lint_parser(doc, raw)
    validation: dict[str, Any] = {
        "lint": "pass" if lint_ok else "fail",
        "findings": [f.as_dict() for f in findings],
        "tool_trace": result.tool_trace,
    }

    query = str(doc.get("FunctionQuery") or "")
    actual_alias = str(doc.get("FunctionAlias") or fallback_alias)

    # Only spend a Log Analytics round trip on a document that survived linting.
    if lint_ok and logs is not None and query.strip():
        probe = await logs.query(f"{query.rstrip()}\n| take 1")
        validation["kql"] = probe.as_dict()
        if not probe.ok:
            findings_kql = {
                "rule": "parser.kql", "severity": "error",
                "message": f"{probe.error_kind}: {probe.error}", "where": "FunctionQuery",
            }
            validation["findings"].append(findings_kql)
            lint_ok = False
    elif logs is None:
        validation["kql"] = {"ok": None, "skipped": "no Log Analytics credentials configured"}

    status = "validated" if lint_ok else "failed"
    summary = {
        "alias": actual_alias,
        "title": ((doc.get("Function") or {}) or {}).get("Title"),
        "table": table,
        "solution": solution,
        "category": doc.get("Category"),
        "query_lines": len(query.splitlines()),
        "errors": sum(1 for f in validation["findings"] if f["severity"] == "error"),
        "warnings": sum(1 for f in validation["findings"] if f["severity"] == "warn"),
    }

    if status == "validated":
        try:
            summary["file"] = write_artifact(
                settings.output_dir, solution=solution,
                kind="parser", name=actual_alias, content=raw,
            )
        except OSError as exc:
            log.warning("parser draft %s: could not write output file: %s", draft_id, exc)
            summary["file_error"] = str(exc)

    await store.update_draft(
        draft_id, status=status, name=actual_alias, artifact=raw,
        summary=summary, validation=validation,
    )
    log.info("parser draft %s -> %s", draft_id, status)

    return {"draft_id": draft_id, "status": status, "summary": summary,
            "validation": validation, "usage": usage}


def _strip_fences(text: str) -> str:
    """Models wrap output in ```yaml fences despite being told not to."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
