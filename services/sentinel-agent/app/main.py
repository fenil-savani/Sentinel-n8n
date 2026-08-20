"""Sidecar HTTP surface.

Called only by n8n over the compose network; nothing here is published to the
host. Two endpoint families, and the split between them is deliberate:

* ``/drafts/{id}/summary`` is small and LLM-safe. The orchestrator agent may
  call it freely.
* ``/drafts/{id}`` returns the whole artifact and is for the n8n deploy
  workflow only. Keeping it behind a separate path is what stops a 178 KB
  workbook from being pulled into the chat context window.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Literal

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .azure.logs import LogsClient
from .azure.token import TokenProvider
from .config import get_settings
from .generators.parser import ParserRequest, generate_parser
from .generators.workbook import WorkbookRequest, generate_workbook
from .lint.rules import lint_parser, lint_workbook
from .llm import build_runtime
from .llm.base import LLMUnavailable
from .store import Store

settings = get_settings()
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("sentinel-agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = Store(settings.database_url)
    await app.state.store.open()

    app.state.tokens = TokenProvider(settings.azure_tenant_id)
    if settings.azure_configured():
        app.state.logs = LogsClient(
            app.state.tokens,
            client_id=settings.logs_client_id,
            client_secret=settings.logs_client_secret,
            workspace_id=settings.azure_workspace_id,
        )
    else:
        # Usable without Azure: KQL validation degrades to a documented skip
        # rather than a hard failure, so generation can be developed offline.
        app.state.logs = None
        log.warning(
            "Azure not configured — KQL validation will be skipped. "
            "Set AZURE_TENANT_ID / AZURE_WORKSPACE_ID / credentials to enable it."
        )

    # Two runtimes: the panel loop runs a smaller model and a tighter output
    # cap, since a panel is ~1 KB while a parser can be 10x that.
    app.state.runtime = build_runtime(settings, max_tokens=16000)
    app.state.panel_runtime = build_runtime(settings, max_tokens=4000)

    log.info(
        "ready: model=%s panel_model=%s azure=%s",
        settings.llm_model, settings.llm_model_panel,
        settings.azure_configured(),
    )
    try:
        yield
    finally:
        await app.state.runtime.aclose()
        await app.state.panel_runtime.aclose()
        if app.state.logs is not None:
            await app.state.logs.aclose()
        await app.state.tokens.aclose()
        await app.state.store.close()


app = FastAPI(title="Sentinel Agent", version="0.1.0", lifespan=lifespan)


# ── request models ──────────────────────────────────────────────────────────

class ParserBody(BaseModel):
    product: str = Field(..., description="e.g. Corelight")
    logtype: str = Field(..., description="e.g. conn")
    table: str = Field(..., description="Custom table the DCR writes to, e.g. Foo_bar_CL")
    sample_data: str | None = None
    sample_ref: str | None = Field(None, description="File under the reference dir")
    reference_parser: str | None = None
    dedup_key: str | None = None
    notes: str | None = None
    session_id: str | None = None


class WorkbookBody(BaseModel):
    parser: str
    product: str
    topic: str
    mode: Literal["generate", "replicate"] = "generate"
    panels: list[str] = Field(default_factory=list)
    reference_ref: str | None = None
    source_dashboard: str | None = None
    parser_fields: list[str] = Field(default_factory=list)
    tabs: list[str] = Field(default_factory=list)
    notes: str | None = None
    session_id: str | None = None


class KqlBody(BaseModel):
    query: str
    timespan: str | None = "P1D"


class LintBody(BaseModel):
    draft_id: str


# ── health ──────────────────────────────────────────────────────────────────

@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "model": settings.llm_model,
        "panel_model": settings.llm_model_panel,
        "azure_configured": settings.azure_configured(),
        "panel_query_validation": settings.validate_panel_queries,
    }


# ── generation ──────────────────────────────────────────────────────────────

@app.post("/generate/parser")
async def post_generate_parser(body: ParserBody) -> dict[str, Any]:
    try:
        return await generate_parser(
            ParserRequest(**body.model_dump()),
            runtime=app.state.runtime,
            settings=settings,
            store=app.state.store,
            logs=app.state.logs,
        )
    except LLMUnavailable as exc:
        # 503, not 500: the model backend is down, nothing was generated, and a
        # retry is the right response. Distinct from a draft that failed to lint.
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/generate/workbook")
async def post_generate_workbook(body: WorkbookBody) -> dict[str, Any]:
    try:
        return await generate_workbook(
            WorkbookRequest(**body.model_dump()),
            runtime=app.state.runtime,
            panel_runtime=app.state.panel_runtime,
            settings=settings,
            store=app.state.store,
            logs=app.state.logs,
        )
    except LLMUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ── validation ──────────────────────────────────────────────────────────────

@app.post("/validate/kql")
async def post_validate_kql(body: KqlBody) -> dict[str, Any]:
    if app.state.logs is None:
        return {
            "ok": None,
            "skipped": "no Log Analytics credentials configured",
        }
    result = await app.state.logs.query(body.query, timespan=body.timespan)
    return result.as_dict()


@app.post("/lint")
async def post_lint(body: LintBody) -> dict[str, Any]:
    draft = await app.state.store.get_draft(body.draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"no draft {body.draft_id}")
    artifact = draft.get("artifact")
    if not artifact:
        raise HTTPException(status_code=409, detail="draft has no artifact to lint")

    if draft["kind"] == "parser":
        try:
            doc = yaml.safe_load(artifact)
        except yaml.YAMLError as exc:
            return {"pass": False, "findings": [
                {"rule": "parser.yaml", "severity": "error", "message": str(exc)[:400]}
            ]}
        ok, findings = lint_parser(doc if isinstance(doc, dict) else {}, artifact)
    else:
        try:
            doc = json.loads(artifact)
        except json.JSONDecodeError as exc:
            return {"pass": False, "findings": [
                {"rule": "workbook.json", "severity": "error", "message": str(exc)[:400]}
            ]}
        ok, findings = lint_workbook(doc, parser=(draft.get("summary") or {}).get("parser"))

    return {"pass": ok, "findings": [f.as_dict() for f in findings]}


# ── drafts ──────────────────────────────────────────────────────────────────

@app.get("/drafts/{draft_id}/summary")
async def get_draft_summary(draft_id: str) -> dict[str, Any]:
    """Small projection. Safe for the orchestrator agent to call."""
    summary = await app.state.store.get_summary(draft_id)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"no draft {draft_id}")
    return summary


@app.get("/drafts/{draft_id}")
async def get_draft(draft_id: str) -> dict[str, Any]:
    """Full artifact. For the n8n deploy workflow only — never give this to the
    agent, or a 178 KB workbook lands in the chat context."""
    draft = await app.state.store.get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"no draft {draft_id}")
    draft["created_at"] = draft["created_at"].isoformat()
    draft["updated_at"] = draft["updated_at"].isoformat()
    return draft


@app.get("/drafts")
async def list_drafts(session_id: str | None = None, limit: int = 20) -> dict[str, Any]:
    return {"drafts": await app.state.store.list_drafts(session_id, limit)}


# ── deployment records (written by the n8n deploy workflow) ─────────────────
# One call, after the PUT has already happened: the deploy workflow is a
# single pass (build request -> PUT -> record), so there is no separate
# preflight-phase row to create first and finish later.

class DeploymentBody(BaseModel):
    draft_id: str
    resource_type: Literal["savedSearch", "workbook"]
    resource_id: str
    action: Literal["create", "update"]
    request_body: dict[str, Any] | None = None
    response_body: dict[str, Any] | None = None
    http_status: int | None = None
    status: Literal["deployed", "failed", "unknown"]
    error: str | None = None
    deployed_by: str | None = None


@app.post("/deployments")
async def post_deployment(body: DeploymentBody) -> dict[str, str]:
    dep_id = await app.state.store.record_deployment(**body.model_dump())
    if body.status == "deployed":
        await app.state.store.update_draft(body.draft_id, status="deployed")
    return {"deployment_id": dep_id}


@app.get("/deployments/{dep_id}")
async def get_deployment(dep_id: str) -> dict[str, Any]:
    dep = await app.state.store.get_deployment(dep_id)
    if dep is None:
        raise HTTPException(status_code=404, detail=f"no deployment {dep_id}")
    dep["created_at"] = dep["created_at"].isoformat()
    dep["updated_at"] = dep["updated_at"].isoformat()
    return dep
