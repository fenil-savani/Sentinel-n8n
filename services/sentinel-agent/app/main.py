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
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .azure.logs import LogsClient
from .azure.token import TokenProvider
from .config import get_settings
from .generators.analytic_rule import AnalyticRuleRequest, generate_analytic_rule, revise_analytic_rule
from .generators.parser import ParserRequest, generate_parser, revise_parser
from .generators.tdd import TddRequest, check_tdd_structure, generate_tdd, revise_tdd
from .generators.workbook import WorkbookRequest, generate_workbook
from .lint.rules import lint_analytic_rule, lint_parser, lint_workbook
from .llm import build_runtime, openai_compat
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
        "ready: provider=%s parser_model=%s manifest_model=%s panel_model=%s azure=%s",
        settings.llm_provider, settings.llm_model_parser,
        settings.llm_model_workbook_manifest, settings.llm_model_panel,
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
    solution: str | None = Field(None, description="Solution/vendor name, for the output folder")
    session_id: str | None = None


class AnalyticRuleBody(BaseModel):
    name: str = Field(..., description="Rule name, e.g. Corelight Suspicious DNS Tunneling")
    scenario: str = Field(..., description="Plain-language detection scenario")
    table: str = Field(..., description="Table or parser the rule queries")
    connector_id: str | None = None
    severity: str = "Medium"
    query_frequency: str | None = None
    query_period: str | None = None
    trigger_operator: str | None = None
    trigger_threshold: int | None = None
    create_incident: bool = True
    tactics: list[str] = Field(default_factory=list)
    techniques: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    watchlist: str | None = None
    notes: str | None = None
    solution: str | None = Field(None, description="Solution/vendor name, for the output folder")
    session_id: str | None = None


class TddBody(BaseModel):
    vendor: str = Field(..., description="e.g. Corelight")
    product: str = Field(..., description="e.g. Open NDR Platform")
    purpose: str = Field(..., description="What data flows into Sentinel and why")
    components: list[str] = Field(default_factory=list,
                                   description="Subset of Data Connector, Parser, Analytic Rule, Workbook, Playbook")
    ingestion_mechanism: str | None = None
    api_base_url: str | None = None
    api_auth_type: str | None = None
    api_endpoints: str | None = None
    notes: str | None = None
    solution: str | None = Field(None, description="Solution/vendor name, for the output folder")
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
    solution: str | None = Field(None, description="Solution/vendor name, for the output folder")
    session_id: str | None = None


class RevisionBody(BaseModel):
    feedback: str = Field(..., description="The analyst's requested change, in plain language")
    session_id: str | None = None


class KqlBody(BaseModel):
    query: str
    timespan: str | None = "P1D"


class LintBody(BaseModel):
    draft_id: str


class ChatCompletionBody(BaseModel):
    """OpenAI chat-completions request shape, as sent by n8n's OpenAI Chat
    Model node (LangChain's ChatOpenAI) — see app/llm/openai_compat.py."""
    model_config = ConfigDict(extra="ignore")

    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    stream: bool = False


# ── health ──────────────────────────────────────────────────────────────────

@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "llm_provider": settings.llm_provider,
        "parser_model": settings.llm_model_parser,
        "workbook_manifest_model": settings.llm_model_workbook_manifest,
        "panel_model": settings.llm_model_panel,
        "azure_configured": settings.azure_configured(),
        "panel_query_validation": settings.validate_panel_queries,
    }


# ── orchestrator chat proxy ──────────────────────────────────────────────────
# Lets n8n's own orchestrator chat model (an "OpenAI Chat Model" node pointed
# at this endpoint) run on the claude CLI too — see app/llm/openai_compat.py.
# Independent of settings.llm_provider: this always uses the CLI.

@app.get("/v1/models")
async def get_models() -> dict[str, Any]:
    """Minimal OpenAI-compatible /v1/models. Not used for generation — it
    exists only because n8n's OpenAI credential "test connection" button
    GETs this exact path (see OpenAiApi.credentials.js's `test` block) and
    would otherwise report a 404 as a connection failure."""
    return {
        "object": "list",
        "data": [{
            "id": settings.orchestrator_model,
            "object": "model",
            "owned_by": "anthropic",
        }],
    }


@app.post("/v1/chat/completions", response_model=None)
async def post_chat_completions(body: ChatCompletionBody) -> dict[str, Any] | StreamingResponse:
    try:
        response = await openai_compat.handle(body.model_dump(), settings)
    except LLMUnavailable as exc:
        # OpenAI-shaped error body so LangChain surfaces a sensible message
        # instead of a raw parse failure.
        raise HTTPException(
            status_code=503,
            detail={"error": {"message": str(exc), "type": "api_error"}},
        ) from exc

    if not body.stream:
        return response

    # The claude CLI answers in one shot; there's nothing to relay
    # incrementally. So the completed response above is reframed as an SSE
    # chunk sequence purely so `stream: true` clients (LangChain's
    # ChatOpenAI) get the framing they expect instead of a 400.
    async def _sse():
        for chunk in openai_compat.to_stream_chunks(response):
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_sse(), media_type="text/event-stream")


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


@app.post("/generate/analytic-rule")
async def post_generate_analytic_rule(body: AnalyticRuleBody) -> dict[str, Any]:
    try:
        return await generate_analytic_rule(
            AnalyticRuleRequest(**body.model_dump()),
            runtime=app.state.runtime,
            settings=settings,
            store=app.state.store,
            logs=app.state.logs,
        )
    except LLMUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/generate/tdd")
async def post_generate_tdd(body: TddBody) -> dict[str, Any]:
    try:
        return await generate_tdd(
            TddRequest(**body.model_dump()),
            runtime=app.state.runtime,
            settings=settings,
            store=app.state.store,
        )
    except LLMUnavailable as exc:
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


_REVISERS = {
    "parser": revise_parser,
    "analytic_rule": revise_analytic_rule,
}


@app.post("/revise/{draft_id}")
async def post_revise(draft_id: str, body: RevisionBody) -> dict[str, Any]:
    draft = await app.state.store.get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"no draft {draft_id}")

    kind = draft["kind"]
    if kind == "tdd":
        try:
            return await revise_tdd(
                draft_id, body.feedback,
                runtime=app.state.runtime, settings=settings, store=app.state.store,
            )
        except LLMUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    reviser = _REVISERS.get(kind)
    if reviser is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"revision is not supported for '{kind}' drafts yet — "
                "regenerate with adjusted inputs instead."
            ),
        )
    try:
        return await reviser(
            draft_id, body.feedback,
            runtime=app.state.runtime, settings=settings, store=app.state.store,
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

    if draft["kind"] == "tdd":
        components = (draft.get("summary") or {}).get("components") or []
        findings = check_tdd_structure(artifact, components)
        return {"pass": not any(f["severity"] == "error" for f in findings), "findings": findings}

    if draft["kind"] in ("parser", "analytic_rule"):
        try:
            doc = yaml.safe_load(artifact)
        except yaml.YAMLError as exc:
            return {"pass": False, "findings": [
                {"rule": "parser.yaml", "severity": "error", "message": str(exc)[:400]}
            ]}
        doc = doc if isinstance(doc, dict) else {}
        if draft["kind"] == "analytic_rule":
            ok, findings = lint_analytic_rule(doc, artifact)
        else:
            ok, findings = lint_parser(doc, artifact)
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
