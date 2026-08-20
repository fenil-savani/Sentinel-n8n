"""Log Analytics query API client — the primary validation gate.

Running the generated KQL is worth more than any static check: it proves the
syntax parses *and* that the `*_CL` table and every column referenced actually
exist in the workspace. ARM What-If cannot tell you either of those things.

Three outcomes are distinguished, because they mean very different things to
an analyst:

    ok=True,  rows>0   working parser
    ok=True,  rows==0  valid query, empty table (DCR not flowing yet) -> warn
    ok=False           syntax error or missing table/column           -> block
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from .token import LOGS_SCOPE, AzureAuthError, TokenProvider

LOGS_ENDPOINT = "https://api.loganalytics.io/v1/workspaces/{workspace_id}/query"

#: Azure reports a missing table and a syntax error with the same status. The
#: distinction matters: a missing table usually means the DCR has not run yet,
#: which is not the generator's fault and needs a different message.
_MISSING_TABLE = re.compile(
    r"(failed to resolve table or column expression|"
    r"could not resolve table|unknown table)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class QueryResult:
    ok: bool
    row_count: int = 0
    error: str | None = None
    #: syntax | missing_table | auth | transport | None
    error_kind: str | None = None
    columns: list[str] | None = None

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "row_count": self.row_count,
            "error": self.error,
            "error_kind": self.error_kind,
            "columns": self.columns,
        }


class LogsClient:
    def __init__(
        self,
        tokens: TokenProvider,
        *,
        client_id: str,
        client_secret: str,
        workspace_id: str,
        timeout: float = 120.0,
    ) -> None:
        self._tokens = tokens
        self._client_id = client_id
        self._client_secret = client_secret
        self._workspace_id = workspace_id
        self._http = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def query(self, kql: str, *, timespan: str | None = "P1D") -> QueryResult:
        try:
            token = await self._tokens.get(
                client_id=self._client_id,
                client_secret=self._client_secret,
                scope=LOGS_SCOPE,
            )
        except AzureAuthError as exc:
            return QueryResult(ok=False, error=str(exc), error_kind="auth")

        payload: dict = {"query": kql}
        if timespan:
            payload["timespan"] = timespan

        try:
            resp = await self._http.post(
                LOGS_ENDPOINT.format(workspace_id=self._workspace_id),
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.RequestError as exc:
            return QueryResult(ok=False, error=str(exc), error_kind="transport")

        if resp.status_code == 200:
            body = resp.json()
            tables = body.get("tables") or []
            rows = sum(len(t.get("rows") or []) for t in tables)
            columns = (
                [c.get("name") for c in (tables[0].get("columns") or [])] if tables else []
            )
            return QueryResult(ok=True, row_count=rows, columns=columns)

        message = self._extract_error(resp)
        kind = "missing_table" if _MISSING_TABLE.search(message) else "syntax"
        if resp.status_code in (401, 403):
            kind = "auth"
        return QueryResult(ok=False, error=message, error_kind=kind)

    @staticmethod
    def _extract_error(resp: httpx.Response) -> str:
        """Azure nests the useful part several levels down; the outer message is
        always the useless 'The request had some invalid properties'."""
        try:
            err = resp.json().get("error", {})
        except ValueError:
            return f"HTTP {resp.status_code}: {resp.text[:400]}"

        parts: list[str] = []
        node = err
        seen = 0
        while isinstance(node, dict) and seen < 6:
            if msg := node.get("message"):
                parts.append(str(msg))
            details = node.get("details")
            if isinstance(details, list) and details:
                node = details[0]
            else:
                node = node.get("innererror") or {}
            seen += 1
        return " | ".join(dict.fromkeys(parts)) or f"HTTP {resp.status_code}"
