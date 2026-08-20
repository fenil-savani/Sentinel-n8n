"""Entra ID client-credentials tokens, cached per scope.

The management plane and the Log Analytics query API are **different audiences**
and need separately-scoped tokens from the same (or different) service
principals. Requesting one and using it against the other returns 401 with a
message that does not obviously say "wrong scope", which is the single most
common way to lose an afternoon on this integration.

    management.azure.com/.default   -> deploy (savedSearches, workbooks)
    api.loganalytics.io/.default    -> query  (KQL validation)
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

MANAGEMENT_SCOPE = "https://management.azure.com/.default"
LOGS_SCOPE = "https://api.loganalytics.io/.default"


class AzureAuthError(RuntimeError):
    pass


@dataclass(slots=True)
class _CachedToken:
    value: str
    expires_at: float

    @property
    def valid(self) -> bool:
        # 60s skew so a token never expires mid-flight.
        return time.time() < self.expires_at - 60


class TokenProvider:
    def __init__(self, tenant_id: str, timeout: float = 30.0) -> None:
        self._tenant_id = tenant_id
        self._cache: dict[tuple[str, str], _CachedToken] = {}
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(self, *, client_id: str, client_secret: str, scope: str) -> str:
        if not (self._tenant_id and client_id and client_secret):
            raise AzureAuthError(
                "Azure credentials are not configured "
                "(AZURE_TENANT_ID / CLIENT_ID / CLIENT_SECRET)"
            )

        key = (client_id, scope)
        cached = self._cache.get(key)
        if cached and cached.valid:
            return cached.value

        url = f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token"
        try:
            resp = await self._client.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scope": scope,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.RequestError as exc:
            raise AzureAuthError(f"could not reach Entra ID: {exc}") from exc

        if resp.status_code >= 400:
            raise AzureAuthError(
                f"token request for scope '{scope}' failed with "
                f"{resp.status_code}: {resp.text[:400]}"
            )

        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise AzureAuthError(f"token response contained no access_token: {body}")

        self._cache[key] = _CachedToken(
            value=token, expires_at=time.time() + int(body.get("expires_in", 3600))
        )
        return token
