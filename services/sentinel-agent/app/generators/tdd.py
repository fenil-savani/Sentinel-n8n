"""TDD generation — drives `generate-sentinel-tdd.md`.

The model only authors and submits the markdown; everything after validation
is deterministic (the tool-calling agent has no shell access to run the
skill's own converter itself). Once a draft's markdown is validated, this
module writes it to `<draft_id>.md` — named for the draft's own Postgres id,
so the filename is stable and traceable back to `drafts.id` regardless of
how the vendor/product get renamed on revision — then renders the two
`.drawio` architecture diagrams and a `.docx` via `tdd_docx.build_docx`
(the bundled `generate-sentinel-tdd` skill kit). The `.docx` step is
best-effort: a failure there doesn't fail the draft, since the markdown is
already validated and written by that point.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..llm.base import AgentRuntime, LLMUnavailable, RunResult
from ..output import write_artifact
from ..prompts import tdd_system
from ..store import Store, new_id
from ..tools import Toolbox
from ._shared import (
    handle_pause_or_failure,
    provider_name,
    revision_prompt,
    usage_recorder,
    usage_summary,
)
from .tdd_docx import build_docx

log = logging.getLogger(__name__)

_ANCHOR_HEADINGS = ("Overall System Architecture", "Data Connector Architecture")


@dataclass(slots=True)
class TddRequest:
    vendor: str
    product: str
    purpose: str
    components: list[str] = field(default_factory=list)
    ingestion_mechanism: str | None = None
    api_base_url: str | None = None
    api_auth_type: str | None = None
    api_endpoints: str | None = None
    notes: str | None = None
    solution: str | None = None
    session_id: str | None = None

    def to_prompt(self) -> str:
        lines = [
            "Generate a Microsoft Sentinel TDD with these inputs:",
            "",
            f"- Vendor: {self.vendor}",
            f"- Product: {self.product}",
            f"- Integration purpose: {self.purpose}",
        ]
        if self.components:
            lines.append(f"- Components in scope: {', '.join(self.components)}")
        else:
            lines.append(
                "- Components in scope were not specified — ask with request_input rather than "
                "guessing which of Data Connector/Parser/Analytic Rule/Workbook/Playbook apply."
            )
        if self.ingestion_mechanism:
            lines.append(f"- Ingestion mechanism: {self.ingestion_mechanism}")
        if self.api_base_url:
            lines.append(f"- API base URL: {self.api_base_url}")
        if self.api_auth_type:
            lines.append(f"- API auth type: {self.api_auth_type}")
        if self.api_endpoints:
            lines.append(f"- Known API endpoints/detail: {self.api_endpoints}")
        if self.notes:
            lines.append(f"- Additional context: {self.notes}")
        lines += [
            "",
            "Submit the complete markdown with submit_tdd when you are done.",
        ]
        return "\n".join(lines)


async def generate_tdd(
    request: TddRequest,
    *,
    runtime: AgentRuntime,
    settings: Settings,
    store: Store,
) -> dict[str, Any]:
    name = f"{request.vendor}_{request.product}_TDD"
    draft_id = await store.create_draft(kind="tdd", name=name, session_id=request.session_id)
    run_id = new_id("run")
    on_call = usage_recorder(
        store=store, run_id=run_id, draft_id=draft_id, session_id=request.session_id,
        provider=provider_name(runtime), call_site="tdd",
    )

    toolbox = Toolbox(settings, logs=None)
    tools = toolbox.tdd_tools()
    available = runtime.supported_tool_names(tools)
    log.info(
        "tdd draft %s: starting | provider=%s vendor=%s product=%s",
        draft_id, type(runtime).__name__, request.vendor, request.product,
    )
    try:
        result = await runtime.run(
            system=tdd_system(settings.prompts_dir, available),
            user=request.to_prompt(),
            tools=tools,
            model=settings.llm_model_parser,
            on_call=on_call,
        )
    except LLMUnavailable as exc:
        log.warning("tdd draft %s: LLM unavailable | error=%s", draft_id, exc)
        await store.update_draft(draft_id, status="failed",
                                 validation={"error": "llm_unavailable"})
        raise

    return await _finish(
        draft_id, run_id=run_id, result=result, components=request.components,
        solution=request.solution or request.vendor, settings=settings, store=store,
        fallback_name=name, vendor=request.vendor, product=request.product,
    )


async def revise_tdd(
    draft_id: str,
    feedback: str,
    *,
    runtime: AgentRuntime,
    settings: Settings,
    store: Store,
) -> dict[str, Any]:
    draft = await store.get_draft(draft_id)
    if draft is None or draft["kind"] != "tdd":
        return {"draft_id": draft_id, "status": "failed",
                "error": f"no TDD draft {draft_id}"}

    summary = draft.get("summary") or {}
    run_id = new_id("run")
    on_call = usage_recorder(
        store=store, run_id=run_id, draft_id=draft_id, session_id=draft.get("session_id"),
        provider=provider_name(runtime), call_site="tdd",
    )
    toolbox = Toolbox(settings, logs=None)
    tools = toolbox.tdd_tools()
    available = runtime.supported_tool_names(tools)
    user = revision_prompt(
        kind_label="Technical Design Document", current_artifact=draft["artifact"] or "",
        feedback=feedback, submit_tool="submit_tdd",
    )
    log.info("tdd draft %s: revising | feedback=%s", draft_id, feedback[:200])
    try:
        result = await runtime.run(
            system=tdd_system(settings.prompts_dir, available),
            user=user, tools=tools, model=settings.llm_model_parser,
            on_call=on_call,
        )
    except LLMUnavailable as exc:
        log.warning("tdd draft %s: LLM unavailable during revision | error=%s", draft_id, exc)
        await store.update_draft(draft_id, status="failed",
                                 validation={"error": "llm_unavailable"})
        raise

    return await _finish(
        draft_id, run_id=run_id, result=result, components=summary.get("components") or [],
        solution=summary.get("solution") or draft["name"], settings=settings, store=store,
        fallback_name=draft["name"],
        vendor=summary.get("vendor", ""), product=summary.get("product", ""),
    )


async def _finish(
    draft_id: str,
    *,
    run_id: str,
    result: RunResult,
    components: list[str],
    solution: str,
    settings: Settings,
    store: Store,
    fallback_name: str,
    vendor: str,
    product: str,
) -> dict[str, Any]:
    usage = await usage_summary(store, run_id)

    paused = handle_pause_or_failure(draft_id, result, "submit_tdd")
    if paused is not None:
        response, validation = paused
        log.info("tdd draft %s: %s", draft_id, response["status"])
        await store.update_draft(draft_id, status=response["status"], validation=validation)
        return {**response, "usage": usage}

    markdown = (result.payload or {}).get("markdown", "").strip()
    findings = check_tdd_structure(markdown, components)
    lint_ok = not any(f["severity"] == "error" for f in findings)

    validation: dict[str, Any] = {
        "lint": "pass" if lint_ok else "fail",
        "findings": findings,
        "tool_trace": result.tool_trace,
    }
    status = "validated" if lint_ok else "failed"
    name = fallback_name

    summary = {
        "name": name,
        "vendor": vendor,
        "product": product,
        "components": components,
        "solution": solution,
        "sections": markdown.count("\n# "),
        "tbd_count": markdown.count("<TBD>"),
        "errors": sum(1 for f in findings if f["severity"] == "error"),
        "warnings": sum(1 for f in findings if f["severity"] == "warn"),
    }

    if status == "validated":
        # Filename is the draft's own Postgres id, not the vendor/product —
        # stable across revisions and unambiguous even if two drafts share a
        # vendor/product name.
        try:
            summary["file"] = write_artifact(
                settings.output_dir, solution=solution,
                kind="tdd", name=draft_id, content=markdown,
            )
        except OSError as exc:
            log.warning("tdd draft %s: could not write output file: %s", draft_id, exc)
            summary["file_error"] = str(exc)

        docx_summary = await asyncio.to_thread(
            build_docx,
            settings=settings, solution=solution, draft_id=draft_id,
            vendor=vendor, product=product, components=components,
            markdown=markdown,
            title=f"{vendor} {product} Microsoft Sentinel Integration - "
                  "Technical Design Document",
        )
        summary.update(docx_summary)

    await store.update_draft(
        draft_id, status=status, name=name, artifact=markdown,
        summary=summary, validation=validation,
    )
    log.info("tdd draft %s -> %s", draft_id, status)

    return {"draft_id": draft_id, "status": status, "summary": summary,
            "validation": validation, "usage": usage}


def check_tdd_structure(markdown: str, components: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not markdown:
        findings.append({"rule": "tdd.empty", "severity": "error",
                          "message": "submitted document is empty"})
        return findings

    for heading in _ANCHOR_HEADINGS:
        if heading == "Data Connector Architecture" and not any(
            "connector" in c.lower() for c in components
        ):
            continue
        if f"# {heading}" not in markdown and f"## {heading}" not in markdown:
            findings.append({
                "rule": "tdd.anchor_heading", "severity": "error",
                "message": f"missing the anchor heading '{heading}'",
            })

    if "# Version Control" not in markdown:
        findings.append({"rule": "tdd.version_control", "severity": "warn",
                          "message": "no Version Control section"})
    if "# References" not in markdown:
        findings.append({"rule": "tdd.references", "severity": "warn",
                          "message": "no References section"})

    return findings
