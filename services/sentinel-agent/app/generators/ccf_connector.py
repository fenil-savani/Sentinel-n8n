"""CCF v2 (RestApiPoller) data connector generation — drives
`generate-sentinel-ccf-connector.md`.

One agent run, but the artifact is a **file set** (ConnectorDefinition, PollerConfig, DCR, and
optionally Table), not a single document like every other generator here. That's the one real
structural difference from parser.py, which this module otherwise mirrors closely: the skill
does the hard thinking (auth/pagination pattern selection, KQL transform, cross-file naming),
this module supplies the tools, validates what comes back, and persists it.

There is no live-Azure validation step (unlike parser's run_kql): a CCF file set has nothing to
execute against a workspace, so `lint_ccf_connector` — the deterministic port of the reference
project's reviewer gates — is the whole safety net.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..azure.logs import LogsClient
from ..config import Settings
from ..lint.rules import lint_ccf_connector
from ..llm.base import AgentRuntime, LLMUnavailable, RunResult
from ..output import write_ccf_connector_files
from ..prompts import ccf_connector_system
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
class CcfConnectorRequest:
    company: str
    product: str
    log_type: str
    publisher: str
    auth_type: str
    pagination_type: str
    api_endpoints: str
    response_structure: str
    table: str | None = None
    reference_connector: str | None = None
    notes: str | None = None
    solution: str | None = None
    session_id: str | None = None

    def to_prompt(self, available_tools: Iterable[str], reference_dir: Path) -> str:
        names = set(available_tools)
        has_read_reference = "read_reference" in names
        read_hint = "read_reference" if has_read_reference else "the Read tool"

        lines = [
            "Generate a Microsoft Sentinel CCF v2 RestApiPoller connector with these inputs:",
            "",
            f"- Company: {self.company}",
            f"- Product: {self.product}",
            f"- Log type: {self.log_type}",
            f"- Publisher (shown in Sentinel UI): {self.publisher}",
            f"- Authentication type: {self.auth_type}",
            f"- Pagination type: {self.pagination_type}",
            f"- API endpoint(s): {self.api_endpoints}",
        ]
        if self.table:
            lines.append(f"- Table: {self.table}")
        else:
            lines.append("- Table: no existing table named — decide standard vs. custom "
                          "_CL from the response structure below")
        if self.reference_connector:
            ref_path = (
                f"ccf_connector/{self.reference_connector}"
                if has_read_reference
                else str(reference_dir / "ccf_connector" / self.reference_connector)
            )
            lines.append(
                f"- Closest reference connector to mirror: {ref_path} "
                f"(read its PollerConfig/PollingConfig file with {read_hint} BEFORE writing anything)"
            )
        if self.notes:
            lines.append(f"- Additional context: {self.notes}")
        lines += ["", "API response structure:", "```", self.response_structure.strip()[:20_000], "```"]
        lines += [
            "",
            "Submit the complete file set with submit_ccf_connector when you are done — "
            "verify the cross-file mapping chain yourself first (SKILL.md Step 8).",
        ]
        return "\n".join(lines)


async def generate_ccf_connector(
    request: CcfConnectorRequest,
    *,
    runtime: AgentRuntime,
    settings: Settings,
    store: Store,
    logs: LogsClient | None,
) -> dict[str, Any]:
    alias = _alias(request.company, request.product, request.log_type)
    draft_id = await store.create_draft(
        kind="ccf_connector", name=alias, session_id=request.session_id
    )
    run_id = new_id("run")
    on_call = usage_recorder(
        store=store, run_id=run_id, draft_id=draft_id, session_id=request.session_id,
        provider=provider_name(runtime), call_site="ccf_connector",
    )

    toolbox = Toolbox(settings, logs)
    tools = toolbox.ccf_connector_tools()
    available = runtime.supported_tool_names(tools)
    log.info(
        "ccf_connector draft %s: starting | provider=%s model=%s company=%s product=%s auth=%s paging=%s",
        draft_id, type(runtime).__name__, settings.llm_model_parser,
        request.company, request.product, request.auth_type, request.pagination_type,
    )
    try:
        result = await runtime.run(
            system=ccf_connector_system(settings.prompts_dir, available),
            user=request.to_prompt(available, settings.reference_dir),
            tools=tools,
            model=settings.llm_model_parser,
            on_call=on_call,
        )
    except LLMUnavailable as exc:
        log.warning("ccf_connector draft %s: LLM unavailable | provider=%s model=%s error=%s",
                    draft_id, type(runtime).__name__, settings.llm_model_parser, exc)
        await store.update_draft(draft_id, status="failed",
                                 validation={"error": "llm_unavailable"})
        raise

    return await _finish(
        draft_id, run_id=run_id, result=result,
        solution=request.solution or request.company, settings=settings, store=store,
        fallback_alias=alias,
    )


async def revise_ccf_connector(
    draft_id: str,
    feedback: str,
    *,
    runtime: AgentRuntime,
    settings: Settings,
    store: Store,
    logs: LogsClient | None,
) -> dict[str, Any]:
    draft = await store.get_draft(draft_id)
    if draft is None or draft["kind"] != "ccf_connector":
        return {"draft_id": draft_id, "status": "failed",
                "error": f"no ccf_connector draft {draft_id}"}

    summary = draft.get("summary") or {}
    run_id = new_id("run")
    on_call = usage_recorder(
        store=store, run_id=run_id, draft_id=draft_id, session_id=draft.get("session_id"),
        provider=provider_name(runtime), call_site="ccf_connector",
    )
    toolbox = Toolbox(settings, logs)
    tools = toolbox.ccf_connector_tools()
    available = runtime.supported_tool_names(tools)
    user = revision_prompt(
        kind_label="Microsoft Sentinel CCF v2 connector file set",
        current_artifact=draft["artifact"] or "", feedback=feedback,
        submit_tool="submit_ccf_connector",
    )
    log.info("ccf_connector draft %s: revising | feedback=%s", draft_id, feedback[:200])
    try:
        result = await runtime.run(
            system=ccf_connector_system(settings.prompts_dir, available),
            user=user, tools=tools, model=settings.llm_model_parser,
            on_call=on_call,
        )
    except LLMUnavailable as exc:
        log.warning("ccf_connector draft %s: LLM unavailable during revision | error=%s", draft_id, exc)
        await store.update_draft(draft_id, status="failed",
                                 validation={"error": "llm_unavailable"})
        raise

    return await _finish(
        draft_id, run_id=run_id, result=result,
        solution=summary.get("solution") or draft["name"], settings=settings, store=store,
        fallback_alias=draft["name"],
    )


def _alias(company: str, product: str, log_type: str) -> str:
    return f"{company.lower()}_{product.lower()}_{log_type.lower()}".replace(" ", "_")


async def _finish(
    draft_id: str,
    *,
    run_id: str,
    result: RunResult,
    solution: str,
    settings: Settings,
    store: Store,
    fallback_alias: str,
) -> dict[str, Any]:
    usage = await usage_summary(store, run_id)

    paused = handle_pause_or_failure(draft_id, result, "submit_ccf_connector")
    if paused is not None:
        response, validation = paused
        log.info("ccf_connector draft %s: %s", draft_id, response["status"])
        await store.update_draft(draft_id, status=response["status"], validation=validation)
        return {**response, "usage": usage}

    payload = result.payload or {}
    raw_parts = {
        "ConnectorDefinition": payload.get("connector_definition_json", ""),
        "PollerConfig": payload.get("poller_config_json", ""),
        "DCR": payload.get("dcr_json", ""),
    }
    if payload.get("table_json"):
        raw_parts["Table"] = payload["table_json"]
    raw_combined = "\n".join(raw_parts.values())

    try:
        connector_definition = json.loads(raw_parts["ConnectorDefinition"])
        poller_config = json.loads(raw_parts["PollerConfig"])
        dcr = json.loads(raw_parts["DCR"])
        table = json.loads(raw_parts["Table"]) if "Table" in raw_parts else None
    except json.JSONDecodeError as exc:
        await store.update_draft(
            draft_id, status="failed", artifact=raw_combined,
            validation={"error": "invalid_json", "message": str(exc)[:800]},
        )
        return {"draft_id": draft_id, "status": "failed",
                "error": f"Generated file set does not parse as JSON: {exc}", "usage": usage}

    shape_errors = []
    if not isinstance(connector_definition, dict):
        shape_errors.append("ConnectorDefinition must be a JSON object")
    if not isinstance(poller_config, list):
        shape_errors.append("PollerConfig must be a JSON array")
    if not isinstance(dcr, list):
        shape_errors.append("DCR must be a JSON array")
    if table is not None and not isinstance(table, list):
        shape_errors.append("Table must be a JSON array")
    if shape_errors:
        await store.update_draft(
            draft_id, status="failed", artifact=raw_combined,
            validation={"error": "invalid_shape", "message": "; ".join(shape_errors)},
        )
        return {"draft_id": draft_id, "status": "failed",
                "error": "; ".join(shape_errors), "usage": usage}

    lint_ok, findings = lint_ccf_connector(connector_definition, poller_config, dcr, table, raw_combined)
    validation: dict[str, Any] = {
        "lint": "pass" if lint_ok else "fail",
        "findings": [f.as_dict() for f in findings],
        "tool_trace": result.tool_trace,
    }

    cd_props = (connector_definition.get("properties") or {}).get("connectorUiConfig") or {}
    connector_id = cd_props.get("id") or fallback_alias
    table_names = [t.get("name") for t in (table or []) if t.get("name")]
    auth_types = sorted({(p.get("properties") or {}).get("auth", {}).get("type")
                         for p in poller_config if (p.get("properties") or {}).get("auth")})

    status = "validated" if lint_ok else "failed"
    summary = {
        "connector_id": connector_id,
        "solution": solution,
        "auth_types": auth_types,
        "poller_count": len(poller_config),
        "tables": table_names,
        "errors": sum(1 for f in validation["findings"] if f["severity"] == "error"),
        "warnings": sum(1 for f in validation["findings"] if f["severity"] == "warn"),
    }

    if status == "validated":
        files = {
            "ConnectorDefinition": raw_parts["ConnectorDefinition"],
            "PollerConfig": raw_parts["PollerConfig"],
            "DCR": raw_parts["DCR"],
        }
        if "Table" in raw_parts:
            files["Table"] = raw_parts["Table"]
        try:
            paths = write_ccf_connector_files(
                settings.output_dir, solution=solution, name=connector_id, files=files,
            )
            summary["files"] = paths
        except OSError as exc:
            log.warning("ccf_connector draft %s: could not write output files: %s", draft_id, exc)
            summary["file_error"] = str(exc)

    artifact = json.dumps(
        {
            "connector_definition": connector_definition,
            "poller_config": poller_config,
            "dcr": dcr,
            "table": table,
        },
        indent=2,
    )
    await store.update_draft(
        draft_id, status=status, name=connector_id, artifact=artifact,
        summary=summary, validation=validation,
    )
    log.info("ccf_connector draft %s -> %s", draft_id, status)

    return {"draft_id": draft_id, "status": status, "summary": summary,
            "validation": validation, "usage": usage}
