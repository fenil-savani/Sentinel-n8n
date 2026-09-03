"""Analytic rule generation — drives `generate-sentinel-analytic-rule.md`.

Same shape as `generators/parser.py`: one agent run, one YAML artifact,
structural lint before an optional live KQL probe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import yaml

from ..azure.logs import LogsClient
from ..config import Settings
from ..lint.rules import lint_analytic_rule
from ..llm.base import AgentRuntime, LLMUnavailable, RunResult
from ..output import write_artifact
from ..prompts import analytic_rule_system
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


@dataclass(slots=True)
class AnalyticRuleRequest:
    name: str
    scenario: str
    table: str
    connector_id: str | None = None
    severity: str = "Medium"
    query_frequency: str | None = None
    query_period: str | None = None
    trigger_operator: str | None = None
    trigger_threshold: int | None = None
    create_incident: bool = True
    tactics: list[str] = field(default_factory=list)
    techniques: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    watchlist: str | None = None
    notes: str | None = None
    solution: str | None = None
    session_id: str | None = None

    def to_prompt(self) -> str:
        lines = [
            "Generate a Microsoft Sentinel analytic rule with these inputs:",
            "",
            f"- Rule name: {self.name}",
            f"- Detection scenario: {self.scenario}",
            f"- Table/parser to query: {self.table}",
            f"- Severity: {self.severity}",
        ]
        if self.connector_id:
            lines.append(f"- Connector id (requiredDataConnectors.connectorId): {self.connector_id}")
        else:
            lines.append(
                "- No connector id was given — ask for it with request_input rather than inventing one."
            )
        if self.query_frequency or self.query_period:
            lines.append(
                f"- Trigger schedule: queryFrequency={self.query_frequency or '<propose one>'}, "
                f"queryPeriod={self.query_period or '<propose one>'}"
            )
        if self.trigger_operator or self.trigger_threshold is not None:
            lines.append(
                f"- Trigger condition: triggerOperator={self.trigger_operator or 'gt'}, "
                f"triggerThreshold={self.trigger_threshold if self.trigger_threshold is not None else 0}"
            )
        lines.append(f"- Auto-create incident: {self.create_incident}")
        if self.tactics:
            lines.append(f"- Tactics (already chosen): {', '.join(self.tactics)}")
        if self.techniques:
            lines.append(f"- Techniques (already chosen): {', '.join(self.techniques)}")
        if not self.tactics or not self.techniques:
            lines.append(
                "- Tactics/techniques not fully specified — derive real MITRE ATT&CK values from "
                "the scenario yourself; do not leave them empty and do not invent fake ids."
            )
        if self.entities:
            lines.append(f"- Entities to map: {', '.join(self.entities)}")
        if self.watchlist:
            lines.append(f"- Use watchlist '{self.watchlist}' for exclusions instead of hardcoding values.")
        if self.notes:
            lines.append(f"- Additional context: {self.notes}")
        lines += [
            "",
            "Submit the complete YAML with submit_analytic_rule when you are done.",
        ]
        return "\n".join(lines)


async def generate_analytic_rule(
    request: AnalyticRuleRequest,
    *,
    runtime: AgentRuntime,
    settings: Settings,
    store: Store,
    logs: LogsClient | None,
) -> dict[str, Any]:
    draft_id = await store.create_draft(
        kind="analytic_rule", name=request.name, session_id=request.session_id
    )
    run_id = new_id("run")
    on_call = usage_recorder(
        store=store, run_id=run_id, draft_id=draft_id, session_id=request.session_id,
        provider=provider_name(runtime), call_site="analytic_rule",
    )

    toolbox = Toolbox(settings, logs)
    tools = toolbox.analytic_rule_tools()
    available = runtime.supported_tool_names(tools)
    log.info(
        "analytic_rule draft %s: starting | provider=%s model=%s name=%s table=%s",
        draft_id, type(runtime).__name__, settings.llm_model_parser, request.name, request.table,
    )
    try:
        result = await runtime.run(
            system=analytic_rule_system(settings.prompts_dir, available),
            user=request.to_prompt(),
            tools=tools,
            model=settings.llm_model_parser,
            on_call=on_call,
        )
    except LLMUnavailable as exc:
        log.warning("analytic_rule draft %s: LLM unavailable | error=%s", draft_id, exc)
        await store.update_draft(draft_id, status="failed",
                                 validation={"error": "llm_unavailable"})
        raise

    return await _finish(
        draft_id, run_id=run_id, result=result, table=request.table,
        solution=request.solution or request.name, settings=settings, store=store, logs=logs,
        fallback_name=request.name,
    )


async def revise_analytic_rule(
    draft_id: str,
    feedback: str,
    *,
    runtime: AgentRuntime,
    settings: Settings,
    store: Store,
    logs: LogsClient | None,
) -> dict[str, Any]:
    draft = await store.get_draft(draft_id)
    if draft is None or draft["kind"] != "analytic_rule":
        return {"draft_id": draft_id, "status": "failed",
                "error": f"no analytic rule draft {draft_id}"}

    summary = draft.get("summary") or {}
    run_id = new_id("run")
    on_call = usage_recorder(
        store=store, run_id=run_id, draft_id=draft_id, session_id=draft.get("session_id"),
        provider=provider_name(runtime), call_site="analytic_rule",
    )
    toolbox = Toolbox(settings, logs)
    tools = toolbox.analytic_rule_tools()
    available = runtime.supported_tool_names(tools)
    user = revision_prompt(
        kind_label="Microsoft Sentinel analytic rule", current_artifact=draft["artifact"] or "",
        feedback=feedback, submit_tool="submit_analytic_rule",
    )
    log.info("analytic_rule draft %s: revising | feedback=%s", draft_id, feedback[:200])
    try:
        result = await runtime.run(
            system=analytic_rule_system(settings.prompts_dir, available),
            user=user, tools=tools, model=settings.llm_model_parser,
            on_call=on_call,
        )
    except LLMUnavailable as exc:
        log.warning("analytic_rule draft %s: LLM unavailable during revision | error=%s", draft_id, exc)
        await store.update_draft(draft_id, status="failed",
                                 validation={"error": "llm_unavailable"})
        raise

    return await _finish(
        draft_id, run_id=run_id, result=result, table=summary.get("table", ""),
        solution=summary.get("solution") or draft["name"], settings=settings, store=store, logs=logs,
        fallback_name=draft["name"],
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
    fallback_name: str,
) -> dict[str, Any]:
    usage = await usage_summary(store, run_id)

    paused = handle_pause_or_failure(draft_id, result, "submit_analytic_rule")
    if paused is not None:
        response, validation = paused
        log.info("analytic_rule draft %s: %s", draft_id, response["status"])
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

    lint_ok, findings = lint_analytic_rule(doc, raw)
    validation: dict[str, Any] = {
        "lint": "pass" if lint_ok else "fail",
        "findings": [f.as_dict() for f in findings],
        "tool_trace": result.tool_trace,
    }

    query = str(doc.get("query") or "")
    actual_name = str(doc.get("name") or fallback_name)

    if lint_ok and logs is not None and query.strip():
        probe = await logs.query(f"{query.rstrip()}\n| take 1")
        validation["kql"] = probe.as_dict()
        if not probe.ok:
            validation["findings"].append({
                "rule": "rule.kql", "severity": "error",
                "message": f"{probe.error_kind}: {probe.error}", "where": "query",
            })
            lint_ok = False
    elif logs is None:
        validation["kql"] = {"ok": None, "skipped": "no Log Analytics credentials configured"}

    status = "validated" if lint_ok else "failed"
    summary = {
        "name": actual_name,
        "severity": doc.get("severity"),
        "table": table,
        "solution": solution,
        "tactics": doc.get("tactics"),
        "techniques": doc.get("techniques"),
        "errors": sum(1 for f in validation["findings"] if f["severity"] == "error"),
        "warnings": sum(1 for f in validation["findings"] if f["severity"] == "warn"),
    }

    if status == "validated":
        try:
            summary["file"] = write_artifact(
                settings.output_dir, solution=solution,
                kind="analytic_rule", name=actual_name, content=raw,
            )
        except OSError as exc:
            log.warning("analytic_rule draft %s: could not write output file: %s", draft_id, exc)
            summary["file_error"] = str(exc)

    await store.update_draft(
        draft_id, status=status, name=actual_name, artifact=raw,
        summary=summary, validation=validation,
    )
    log.info("analytic_rule draft %s -> %s", draft_id, status)

    return {"draft_id": draft_id, "status": status, "summary": summary,
            "validation": validation, "usage": usage}


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
