"""Postgres-backed draft and deployment store.

This is what keeps 178 KB artifacts out of the LLM context window. Generators
write the artifact here and hand back only an id plus a small summary; the
orchestrator reasons over the summary, and the n8n deploy workflow reads the
artifact back at PUT time. A consequence worth stating: an approval turn hours
after generation still resolves, because nothing depends on conversation memory.
"""

from __future__ import annotations

import json
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from ulid import ULID


def new_id(prefix: str) -> str:
    return f"{prefix}_{ULID()}"


class Store:
    def __init__(self, dsn: str) -> None:
        self._pool = AsyncConnectionPool(dsn, min_size=1, max_size=8, open=False)

    async def open(self) -> None:
        await self._pool.open(wait=True, timeout=30)

    async def close(self) -> None:
        await self._pool.close()

    # ── drafts ───────────────────────────────────────────────────────────

    async def create_draft(
        self, *, kind: str, name: str, session_id: str | None = None
    ) -> str:
        draft_id = new_id("drf")
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO drafts (id, kind, name, status, session_id) "
                "VALUES (%s, %s, %s, 'generating', %s)",
                (draft_id, kind, name, session_id),
            )
        return draft_id

    async def update_draft(
        self,
        draft_id: str,
        *,
        status: str | None = None,
        name: str | None = None,
        artifact: str | None = None,
        summary: dict[str, Any] | None = None,
        validation: dict[str, Any] | None = None,
        failures: list[dict[str, Any]] | None = None,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("status", status),
            ("name", name),
            ("artifact", artifact),
        ):
            if value is not None:
                sets.append(f"{column} = %s")
                params.append(value)
        for column, value in (
            ("summary", summary),
            ("validation", validation),
            ("failures", failures),
        ):
            if value is not None:
                sets.append(f"{column} = %s::jsonb")
                params.append(json.dumps(value))
        if not sets:
            return
        params.append(draft_id)
        async with self._pool.connection() as conn:
            await conn.execute(f"UPDATE drafts SET {', '.join(sets)} WHERE id = %s", params)

    async def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.cursor(row_factory=dict_row).execute(
                "SELECT * FROM drafts WHERE id = %s", (draft_id,)
            )
            return await cur.fetchone()

    async def get_summary(self, draft_id: str) -> dict[str, Any] | None:
        """The LLM-safe projection. Deliberately never returns `artifact`."""
        async with self._pool.connection() as conn:
            cur = await conn.cursor(row_factory=dict_row).execute(
                "SELECT id, kind, name, status, summary, validation, failures, "
                "       octet_length(coalesce(artifact, '')) AS size_bytes, created_at "
                "FROM drafts WHERE id = %s",
                (draft_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        row["created_at"] = row["created_at"].isoformat()
        return row

    async def list_drafts(self, session_id: str | None, limit: int = 20) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, kind, name, status, created_at FROM drafts "
            "{where} ORDER BY created_at DESC LIMIT %s"
        )
        params: list[Any] = []
        where = ""
        if session_id:
            where = "WHERE session_id = %s"
            params.append(session_id)
        params.append(limit)
        async with self._pool.connection() as conn:
            cur = await conn.cursor(row_factory=dict_row).execute(
                sql.format(where=where), params
            )
            rows = await cur.fetchall()
        for row in rows:
            row["created_at"] = row["created_at"].isoformat()
        return rows

    # ── deployments ──────────────────────────────────────────────────────
    # One row per deploy attempt, written once the PUT has already happened —
    # there is no separate preflight phase to record ahead of it.

    async def record_deployment(
        self,
        *,
        draft_id: str,
        resource_type: str,
        resource_id: str,
        action: str,
        request_body: dict[str, Any] | None,
        response_body: dict[str, Any] | None,
        http_status: int | None,
        status: str,
        error: str | None = None,
        deployed_by: str | None = None,
    ) -> str:
        dep_id = new_id("dep")
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO deployments (id, draft_id, resource_type, resource_id, "
                "action, request_body, response_body, http_status, status, error, "
                "deployed_by) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, "
                "%s, %s, %s)",
                (
                    dep_id,
                    draft_id,
                    resource_type,
                    resource_id,
                    action,
                    json.dumps(request_body) if request_body is not None else None,
                    json.dumps(response_body) if response_body is not None else None,
                    http_status,
                    status,
                    error,
                    deployed_by,
                ),
            )
        return dep_id

    async def get_deployment(self, dep_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.cursor(row_factory=dict_row).execute(
                "SELECT * FROM deployments WHERE id = %s", (dep_id,)
            )
            return await cur.fetchone()
