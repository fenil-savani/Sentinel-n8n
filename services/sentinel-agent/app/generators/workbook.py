"""Workbook generation — plan, then panels, then deterministic assembly.

    stage 1  one call   -> panel manifest (~2-4 KB regardless of dashboard size)
    stage 2  N calls    -> one small object per panel, concurrent, retried
    stage 3  no calls   -> Python assembles the workbook from templates

The split exists because a finished workbook is far too large for one
generation: the shipped reference is 178 KB across 108 panels. Asking any model
for that in one shot fails; asking a small local model for ~1 KB, N times, with
per-item retries, does not.

A run that produces 96 of 100 panels is still worth keeping: the draft is
stored as `validated` with the 4 failures named in `failures`, so the analyst
can see exactly what's missing before deciding whether to deploy it as-is or
regenerate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..azure.logs import LogsClient
from ..config import Settings
from ..lint.rules import lint_workbook
from ..llm.base import AgentRuntime, LLMUnavailable, UsageCallback
from ..output import write_artifact
from ..prompts import workbook_manifest_system, workbook_panel_system
from ..store import Store, new_id
from ..tools import Toolbox
from ..workbook import panel_templates as T
from ._shared import provider_name, usage_recorder, usage_summary

log = logging.getLogger(__name__)


@dataclass(slots=True)
class WorkbookRequest:
    parser: str
    product: str
    topic: str
    mode: str = "generate"  # generate | replicate
    panels: list[str] = field(default_factory=list)
    reference_ref: str | None = None
    source_dashboard: str | None = None
    parser_fields: list[str] = field(default_factory=list)
    tabs: list[str] = field(default_factory=list)
    notes: str | None = None
    solution: str | None = None
    session_id: str | None = None
    #: Additional parsers, for a workbook spanning more than one log type/table.
    #: `parser` above stays the primary/default; every entry here plus `parser`
    #: itself is a valid per-panel choice. Empty (the common case) means every
    #: panel implicitly uses `parser` — fully backward compatible.
    parsers: list[str] = field(default_factory=list)

    @property
    def all_parsers(self) -> list[str]:
        seen: list[str] = []
        for p in (self.parser, *self.parsers):
            if p and p not in seen:
                seen.append(p)
        return seen

    def to_prompt(self, available_tools: Iterable[str], reference_dir: Path) -> str:
        # See ParserRequest.to_prompt: not every runtime offers
        # read_reference/run_python (e.g. ClaudeCliRuntime has neither), and
        # the CLI's native Read tool needs the real absolute path since
        # --add-dir doesn't make a bare filename discoverable.
        names = set(available_tools)
        has_read_reference = "read_reference" in names
        has_run_python = "run_python" in names
        read_hint = "read_reference" if has_read_reference else "the Read tool"
        inspect_hint = "run_python" if has_run_python else "the Read tool"

        lines = [
            f"Plan a Microsoft Sentinel workbook in **{self.mode}** mode.",
            "",
        ]
        if self.parsers:
            all_parsers = self.all_parsers
            lines.append(f"- Parsers in scope ({len(all_parsers)}): {', '.join(all_parsers)}")
            lines.append(
                "  This workbook spans multiple parsers — every panel in your plan MUST set "
                "its own \"parser\" field to exactly one of the names above. Use one tab per "
                "parser as the data-type switcher, and skip the Step 4 per-dimension "
                "multiselect filters entirely (see SKILL.md's Multi-parser workbooks note)."
            )
        else:
            lines.append(f"- Parser to query: {self.parser}")
        lines += [
            f"- Product: {self.product}",
            f"- Topic: {self.topic}",
        ]
        if self.parser_fields:
            lines.append(f"- Fields the parser exposes: {', '.join(self.parser_fields)}")
        if self.panels:
            lines.append(f"- Panels requested: {', '.join(self.panels)}")
            lines.append(
                "  Plan exactly these panels. Do not invent extras beyond what was asked."
            )
        if self.tabs:
            lines.append(f"- Tabs: {', '.join(self.tabs)}")
        if self.reference_ref:
            ref_path = self.reference_ref if has_read_reference \
                else str(reference_dir / self.reference_ref)
            lines.append(
                f"- Reference dashboard: {ref_path} "
                f"(read it with {read_hint}, or inspect it with {inspect_hint} if large)"
            )
        if self.source_dashboard:
            lines += [
                "",
                "Source dashboard to replicate (list every panel, then map each field "
                "to the parser's equivalent):",
                "```",
                self.source_dashboard[:40_000],
                "```",
            ]
        if self.notes:
            lines.append(f"- Additional context: {self.notes}")
        lines += ["", "Submit the plan with submit_manifest."]
        return "\n".join(lines)


async def generate_workbook(
    request: WorkbookRequest,
    *,
    runtime: AgentRuntime,
    panel_runtime: AgentRuntime,
    settings: Settings,
    store: Store,
    logs: LogsClient | None,
) -> dict[str, Any]:
    draft_id = await store.create_draft(
        kind="workbook",
        name=f"{request.product} {request.topic}",
        session_id=request.session_id,
    )
    # One run_id covers both the manifest-stage call and every panel-stage
    # call below — a single /generate/workbook request is one "run" even
    # though it's several dozen LLM calls under the hood.
    run_id = new_id("run")
    manifest_on_call = usage_recorder(
        store=store, run_id=run_id, draft_id=draft_id, session_id=request.session_id,
        provider=provider_name(runtime), call_site="workbook_manifest",
    )
    panel_on_call = usage_recorder(
        store=store, run_id=run_id, draft_id=draft_id, session_id=request.session_id,
        provider=provider_name(panel_runtime), call_site="workbook_panel",
    )
    toolbox = Toolbox(settings, logs)
    manifest_tools = toolbox.manifest_tools()
    available = runtime.supported_tool_names(manifest_tools)

    # ── stage 1: manifest ────────────────────────────────────────────────
    log.info(
        "workbook draft %s: starting manifest | provider=%s model=%s product=%s topic=%s mode=%s",
        draft_id, type(runtime).__name__, settings.llm_model_workbook_manifest,
        request.product, request.topic, request.mode,
    )
    try:
        plan = await runtime.run(
            system=workbook_manifest_system(settings.prompts_dir, available),
            user=request.to_prompt(available, settings.reference_dir),
            tools=manifest_tools,
            model=settings.llm_model_workbook_manifest,
            on_call=manifest_on_call,
        )
    except LLMUnavailable as exc:
        log.warning(
            "workbook draft %s: LLM unavailable at manifest stage | provider=%s model=%s error=%s",
            draft_id, type(runtime).__name__, settings.llm_model_workbook_manifest, exc,
        )
        await store.update_draft(draft_id, status="failed",
                                 validation={"error": "llm_unavailable", "stage": "manifest"})
        raise

    if plan.terminal_tool == "request_input":
        payload = plan.payload or {}
        log.info("workbook draft %s: needs_input at manifest stage", draft_id)
        await store.update_draft(draft_id, status="needs_input",
                                 validation={"needs_input": payload, "stage": "manifest"})
        return {"draft_id": draft_id, "status": "needs_input",
                "missing": payload.get("missing", []),
                "question": payload.get("question", ""),
                "usage": await usage_summary(store, run_id)}

    if plan.terminal_tool != "submit_manifest":
        log.warning(
            "workbook draft %s: no_manifest | terminal_tool=%s text=%s",
            draft_id, plan.terminal_tool, plan.text[:300],
        )
        await store.update_draft(
            draft_id, status="failed",
            validation={"error": "no_manifest", "stage": "manifest",
                        "text": plan.text[:2000]},
        )
        return {"draft_id": draft_id, "status": "failed",
                "error": "The model finished without submitting a panel plan.",
                "usage": await usage_summary(store, run_id)}

    manifest = plan.payload or {}
    specs: list[dict[str, Any]] = manifest.get("panels") or []
    if not specs:
        await store.update_draft(draft_id, status="failed",
                                 validation={"error": "empty_manifest"})
        return {"draft_id": draft_id, "status": "failed",
                "error": "The panel plan came back empty.",
                "usage": await usage_summary(store, run_id)}

    if request.parsers:
        # Multi-parser mode: every panel must name a valid parser explicitly.
        # Checked here, right after the manifest, rather than left to silently
        # default — a wrong-but-unnoticed default is exactly the defect this
        # feature exists to eliminate, and there is no later retry point that
        # can fix a manifest-stage omission (the panel-writing stage only
        # writes KQL, it doesn't re-choose which parser it's for).
        bad = [
            s.get("id") or s.get("title") or "<unnamed>"
            for s in specs
            if (s.get("parser") or "").strip() not in request.all_parsers
        ]
        if bad:
            await store.update_draft(
                draft_id, status="failed",
                validation={"error": "missing_panel_parser", "stage": "manifest",
                            "panels": bad, "valid_parsers": request.all_parsers},
            )
            return {
                "draft_id": draft_id, "status": "failed",
                "error": (
                    f"{len(bad)} panel(s) did not name a valid parser from "
                    f"{request.all_parsers}: {bad}. Regenerate with an explicit "
                    "\"parser\" field on every panel."
                ),
                "usage": await usage_summary(store, run_id),
            }

    title = manifest.get("title") or f"{request.product} {request.topic}"
    # Multi-parser mode: dimension filters assume one shared schema (SKILL.md's
    # Multi-parser workbooks note tells the model to skip them, but this is the
    # Python-enforced guarantee — a filter field that doesn't exist on every
    # parser is exactly the defect class that motivated per-panel parsers).
    parameters = (
        [] if request.parsers
        else _normalise_parameters(manifest.get("parameters"), request.parser)
    )

    await store.update_draft(
        draft_id, name=title,
        summary={"stage": "panels", "planned": len(specs), "title": title},
    )
    log.info("draft %s: manifest has %d panel(s)", draft_id, len(specs))

    # ── stage 2: panels ──────────────────────────────────────────────────
    results = await _generate_panels(
        specs,
        request=request,
        parameters=parameters,
        panel_runtime=panel_runtime,
        settings=settings,
        toolbox=toolbox,
        logs=logs,
        on_call=panel_on_call,
    )
    built = [r for r in results if r.get("ok")]
    failures = [
        {"id": r["id"], "title": r.get("title", ""), "error": r.get("error", "")}
        for r in results
        if not r.get("ok")
    ]

    if not built:
        await store.update_draft(
            draft_id, status="failed", failures=failures,
            validation={"error": "all_panels_failed", "stage": "panels"},
        )
        return {"draft_id": draft_id, "status": "failed",
                "error": f"All {len(specs)} panels failed to generate.",
                "failures": failures, "usage": await usage_summary(store, run_id)}

    # ── stage 3: assemble ────────────────────────────────────────────────
    workbook = _assemble(
        title=title, request=request, parameters=parameters, panels=built
    )
    artifact = json.dumps(workbook, indent=2)

    lint_ok, findings = lint_workbook(workbook, parser=request.all_parsers)
    validation = {
        "lint": "pass" if lint_ok else "fail",
        "findings": [f.as_dict() for f in findings],
        "panel_queries_validated": settings.validate_panel_queries,
    }

    status = "validated" if lint_ok else "failed"

    summary = {
        "title": title,
        "parser": request.parser,
        "parsers": request.all_parsers,
        "panel_count": len(built),
        "planned": len(specs),
        "failed_panels": len(failures),
        "tabs": request.tabs or [],
        "groups": sorted({p["spec"].get("group", "General") for p in built}),
        "errors": sum(1 for f in findings if f.severity == "error"),
        "warnings": sum(1 for f in findings if f.severity == "warn"),
    }

    if status == "validated":
        try:
            summary["file"] = write_artifact(
                settings.output_dir, solution=request.solution or request.product,
                kind="workbook", name=title, content=artifact,
            )
        except OSError as exc:
            log.warning("workbook draft %s: could not write output file: %s", draft_id, exc)
            summary["file_error"] = str(exc)

    await store.update_draft(
        draft_id, status=status, name=title, artifact=artifact,
        summary=summary, validation=validation, failures=failures,
    )
    log.info("workbook draft %s -> %s (%d/%d panels)",
             draft_id, status, len(built), len(specs))

    return {"draft_id": draft_id, "status": status, "summary": summary,
            "validation": validation, "failures": failures,
            "usage": await usage_summary(store, run_id)}


# ── stage 2 helpers ─────────────────────────────────────────────────────────

async def _generate_panels(
    specs: list[dict[str, Any]],
    *,
    request: WorkbookRequest,
    parameters: list[dict[str, Any]],
    panel_runtime: AgentRuntime,
    settings: Settings,
    toolbox: Toolbox,
    logs: LogsClient | None,
    on_call: UsageCallback | None = None,
) -> list[dict[str, Any]]:
    tools = toolbox.panel_tools()
    system = workbook_panel_system(settings.prompts_dir, panel_runtime.supported_tool_names(tools))
    semaphore = asyncio.Semaphore(max(1, settings.panel_concurrency))

    async def one(spec: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await _generate_panel(
                spec, request=request, parameters=parameters, system=system,
                tools=tools, panel_runtime=panel_runtime, settings=settings, logs=logs,
                on_call=on_call,
            )

    # return_exceptions so one unexpected error cannot void the whole run.
    gathered = await asyncio.gather(*(one(s) for s in specs), return_exceptions=True)

    out: list[dict[str, Any]] = []
    for spec, item in zip(specs, gathered):
        if isinstance(item, BaseException):
            out.append({"ok": False, "id": spec.get("id", "?"),
                        "title": spec.get("title", ""), "error": str(item)[:300]})
        else:
            out.append(item)
    return out


async def _generate_panel(
    spec: dict[str, Any],
    *,
    request: WorkbookRequest,
    parameters: list[dict[str, Any]],
    system: str,
    tools: list,
    panel_runtime: AgentRuntime,
    settings: Settings,
    logs: LogsClient | None,
    on_call: UsageCallback | None = None,
) -> dict[str, Any]:
    panel_id = str(spec.get("id") or spec.get("title") or "panel")
    viz = spec.get("viz_type") or "grid"
    panel_parser = (spec.get("parser") or "").strip() or request.parser
    base_prompt = _panel_prompt(spec, request, parameters, panel_parser)
    last_error = "unknown"

    for attempt in range(1, settings.panel_max_retries + 1):
        prompt = base_prompt
        if attempt > 1:
            prompt = (
                f"{base_prompt}\n\nYour previous attempt failed: {last_error}\n"
                "Fix it and submit again."
            )
        try:
            result = await panel_runtime.run(
                system=system, user=prompt, tools=tools,
                model=settings.llm_model_panel, max_iterations=8,
                on_call=on_call,
            )
        except LLMUnavailable as exc:
            # Backend trouble is usually transient; keep the remaining attempts.
            last_error = f"backend unavailable: {exc}"
            log.warning("panel %s attempt %d: %s", panel_id, attempt, last_error)
            continue

        if result.terminal_tool != "submit_panel":
            last_error = f"did not call submit_panel (tool_trace={result.tool_trace})"
            log.warning("panel %s attempt %d: %s", panel_id, attempt, last_error)
            continue

        payload = result.payload or {}
        body = (payload.get("kql") or "").strip()
        if not body:
            last_error = "submitted an empty query"
            log.warning("panel %s attempt %d: %s", panel_id, attempt, last_error)
            continue

        query = T.compose_query(panel_parser, body, parameters)

        if settings.validate_panel_queries and logs is not None:
            probe = await logs.query(T.substitute_parameters(query, parameters))
            if not probe.ok:
                last_error = f"query failed to run ({probe.error_kind}): {probe.error}"
                log.info("panel %s attempt %d rejected: %s", panel_id, attempt, last_error)
                continue

        return {
            "ok": True,
            "id": panel_id,
            "title": payload.get("title") or spec.get("title") or panel_id,
            "viz": viz,
            "parser": panel_parser,
            "query": query,
            "columns": payload.get("columns") or [],
            "value_column": payload.get("value_column"),
            "label_column": payload.get("label_column"),
            "spec": spec,
            "attempts": attempt,
        }

    return {"ok": False, "id": panel_id, "title": spec.get("title", ""), "error": last_error}


def _panel_prompt(
    spec: dict[str, Any], request: WorkbookRequest, parameters: list[dict[str, Any]],
    panel_parser: str,
) -> str:
    lines = [
        "Write the KQL for this single panel.",
        "",
        f"- Parser (base table): {panel_parser}",
        f"- Panel title: {spec.get('title')}",
        f"- Panel id: {spec.get('id')}",
        f"- Visualisation: {spec.get('viz_type')}",
        f"- What it must answer: {spec.get('intent', '')}",
    ]
    if spec.get("fields"):
        lines.append(f"- Fields to use: {', '.join(spec['fields'])}")
    if request.parser_fields and not request.parsers:
        lines.append(f"- Fields available on the parser: {', '.join(request.parser_fields)}")
    if parameters:
        names = ", ".join(f"{{{p['name']}}}" for p in parameters)
        lines.append(
            f"- Filters already applied for you (do NOT write them): "
            f"{{GlobalTimeRestriction}}, {names}"
        )
    lines += [
        "",
        "Return the query pipeline only, starting with '|'. Submit with submit_panel.",
    ]
    return "\n".join(lines)


# ── stage 3 helpers ─────────────────────────────────────────────────────────

def _normalise_parameters(
    raw: list[dict[str, Any]] | None, parser: str
) -> list[dict[str, Any]]:
    """Coerce the model's parameter plan into the shape the templates expect.

    Skill Step 4 fixes the ordering: time range first, then multi-selects, then
    free-text last. Ordering is applied here rather than trusted to the model.
    """
    out: list[dict[str, Any]] = []
    for item in raw or []:
        name = (item.get("name") or "").strip()
        field_name = (item.get("field") or "").strip()
        if not name or not field_name:
            continue
        out.append({
            "name": re.sub(r"\W+", "", name) or "Filter",
            "field": field_name,
            "kind": "text" if item.get("kind") == "text" else "multiselect",
            "label": item.get("label") or name,
        })
    out.sort(key=lambda p: p["kind"] == "text")  # multiselects first, text last
    return out


def _assemble(
    *,
    title: str,
    request: WorkbookRequest,
    parameters: list[dict[str, Any]],
    panels: list[dict[str, Any]],
) -> dict[str, Any]:
    param_items = [T.global_time_parameter()]
    for param in parameters:
        if param["kind"] == "text":
            param_items.append(T.text_parameter(param["name"], param["label"]))
        else:
            param_items.append(
                T.multiselect_parameter(
                    param["name"], param["label"], param["field"], request.parser
                )
            )

    # Preserve manifest order inside each group, and group order by first
    # appearance — an analyst reading the dashboard should meet the overview
    # panels before the detail tables.
    groups: dict[tuple[str | None, str], list[dict[str, Any]]] = {}
    for panel in panels:
        spec = panel["spec"]
        key = (spec.get("tab") or None, spec.get("group") or "General")
        groups.setdefault(key, []).append(panel)

    body: list[dict[str, Any]] = []
    for (tab, group_title), members in groups.items():
        items = [
            T.build_panel(
                panel_id=p["id"],
                title=p["title"],
                query=p["query"],
                viz=p["viz"],
                columns=p["columns"],
                value_column=p["value_column"],
                label_column=p["label_column"],
                width=p["spec"].get("width"),
                export_field=p["spec"].get("export_field"),
            )
            for p in members
        ]
        body.append(T.group_item(group_title, items, tab=tab))

    # `groups` was built by iterating `panels` in manifest order, so its keys
    # already reflect first-appearance order — the tab *selector* item must
    # list every tab the manifest actually used, not just ones the caller
    # pre-declared via request.tabs, or a manifest-only tab's panels get a
    # conditionalVisibility that no link ever sets the "Tab" parameter to,
    # and silently never render.
    discovered_tabs = list(dict.fromkeys(tab for (tab, _group) in groups.keys() if tab))
    tabs = request.tabs or discovered_tabs or None

    return T.build_workbook(
        title=title,
        parser=", ".join(request.all_parsers) if request.parsers else request.parser,
        product=request.product,
        topic=request.topic,
        parameters=param_items,
        body_items=body,
        tabs=tabs,
    )
