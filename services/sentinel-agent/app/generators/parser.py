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
from dataclasses import dataclass
from typing import Any

import yaml

from ..azure.logs import LogsClient
from ..config import Settings
from ..lint.rules import lint_parser
from ..llm.base import AgentRuntime, LLMUnavailable
from ..prompts import parser_system
from ..store import Store
from ..tools import Toolbox

log = logging.getLogger(__name__)


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
    session_id: str | None = None

    def to_prompt(self) -> str:
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
            lines.append(
                f"- Reference parser to mirror: {self.reference_parser} "
                "(read it with read_reference BEFORE writing anything)"
            )
        if self.sample_ref:
            lines.append(
                f"- Sample data file: {self.sample_ref} "
                "(inspect it with run_python; union the keys across EVERY record)"
            )
        if self.notes:
            lines.append(f"- Additional context: {self.notes}")
        if self.sample_data:
            lines += ["", "Sample records:", "```", self.sample_data.strip()[:20_000], "```"]
        lines += [
            "",
            f"The parser's FunctionName and FunctionAlias must both be "
            f"'{self.product.lower()}_{self.logtype.lower()}'.",
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
    alias = f"{request.product.lower()}_{request.logtype.lower()}"
    draft_id = await store.create_draft(
        kind="parser", name=alias, session_id=request.session_id
    )

    toolbox = Toolbox(settings, logs)
    try:
        result = await runtime.run(
            system=parser_system(settings.prompts_dir),
            user=request.to_prompt(),
            tools=toolbox.parser_tools(),
            model=settings.llm_model,
        )
    except LLMUnavailable:
        # Backend problem, not a generation problem. Drop the placeholder row so
        # it does not look like a failed attempt the analyst needs to review.
        await store.update_draft(draft_id, status="failed",
                                 validation={"error": "llm_unavailable"})
        raise

    # The skill's "stop and ask" rule fired — a pause, not a failure.
    if result.terminal_tool == "request_input":
        payload = result.payload or {}
        await store.update_draft(
            draft_id, status="needs_input",
            validation={"needs_input": payload, "tool_trace": result.tool_trace},
        )
        return {
            "draft_id": draft_id,
            "status": "needs_input",
            "missing": payload.get("missing", []),
            "question": payload.get("question", ""),
        }

    if result.terminal_tool != "submit_parser":
        await store.update_draft(
            draft_id, status="failed",
            validation={"error": "no_submission",
                        "message": "the model stopped without calling submit_parser",
                        "text": result.text[:2000], "tool_trace": result.tool_trace},
        )
        return {
            "draft_id": draft_id,
            "status": "failed",
            "error": "The model finished without submitting a parser.",
        }

    raw = _strip_fences((result.payload or {}).get("yaml", ""))

    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        await store.update_draft(
            draft_id, status="failed", artifact=raw,
            validation={"error": "invalid_yaml", "message": str(exc)[:800]},
        )
        return {"draft_id": draft_id, "status": "failed",
                "error": f"Generated YAML does not parse: {exc}"}

    if not isinstance(doc, dict):
        await store.update_draft(
            draft_id, status="failed", artifact=raw,
            validation={"error": "invalid_yaml", "message": "top level is not a mapping"},
        )
        return {"draft_id": draft_id, "status": "failed",
                "error": "Generated YAML is not a mapping."}

    lint_ok, findings = lint_parser(doc, raw)
    validation: dict[str, Any] = {
        "lint": "pass" if lint_ok else "fail",
        "findings": [f.as_dict() for f in findings],
        "tool_trace": result.tool_trace,
    }

    query = str(doc.get("FunctionQuery") or "")
    actual_alias = str(doc.get("FunctionAlias") or alias)

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
        "table": request.table,
        "category": doc.get("Category"),
        "query_lines": len(query.splitlines()),
        "errors": sum(1 for f in validation["findings"] if f["severity"] == "error"),
        "warnings": sum(1 for f in validation["findings"] if f["severity"] == "warn"),
    }

    await store.update_draft(
        draft_id, status=status, name=actual_alias, artifact=raw,
        summary=summary, validation=validation,
    )
    log.info("parser draft %s -> %s", draft_id, status)

    return {"draft_id": draft_id, "status": status, "summary": summary,
            "validation": validation}


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
