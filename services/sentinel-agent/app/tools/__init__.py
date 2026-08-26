"""Tool definitions handed to the agent.

Descriptions here are load-bearing: they are the only place the model learns
*when* to reach for something, and prescriptive "call this when…" wording
measurably raises the hit rate versus a bare name and schema.
"""

from __future__ import annotations

import functools
from pathlib import Path

from ..azure.logs import LogsClient
from ..config import Settings
from ..llm.base import Tool
from .files import ReferenceFiles
from .python_exec import run_python

VIZ_TYPES = ["tiles", "piechart", "barchart", "timechart", "areachart", "grid"]


class Toolbox:
    """Builds the per-stage tool lists with dependencies already bound."""

    def __init__(self, settings: Settings, logs: LogsClient | None) -> None:
        self._settings = settings
        self._logs = logs
        self._files = ReferenceFiles(Path(settings.reference_dir))

    # ── shared, non-terminal ─────────────────────────────────────────────

    def _list_reference(self) -> Tool:
        return Tool(
            name="list_reference_files",
            description=(
                "List the reference artifacts available to you (existing parsers, "
                "example dashboards, sample data). Call this first if you were not "
                "told which reference file to use."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            fn=lambda: self._files.list_files(),
        )

    def _read_reference(self) -> Tool:
        return Tool(
            name="read_reference",
            description=(
                "Read a reference file by relative path (e.g. 'corelight_conn.yaml'). "
                "Call this BEFORE writing anything when a reference parser or example "
                "dashboard exists — you must mirror its structure and conventions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to the reference directory.",
                    }
                },
                "required": ["path"],
            },
            fn=lambda path: self._files.read(path),
        )

    def _run_python(self) -> Tool:
        return Tool(
            name="run_python",
            description=(
                "Run a short Python 3 script and get its stdout back. Call this to "
                "derive a schema from sample data (union the keys across EVERY "
                "record, not just the first), to validate JSON with json.loads, or "
                "for any counting/aggregation work. Prefer this over doing the work "
                "token by token. REFERENCE_DIR is set for you; print() what you want "
                "to see."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python source to execute."}
                },
                "required": ["code"],
            },
            fn=functools.partial(
                run_python,
                timeout=self._settings.python_exec_timeout,
                reference_dir=Path(self._settings.reference_dir),
            ),
        )

    def _run_kql(self) -> Tool:
        async def _query(query: str) -> str:
            if self._logs is None:
                return (
                    "SKIPPED: no Log Analytics credentials configured, so the query "
                    "could not be executed. Write the KQL as carefully as you can; "
                    "it will be validated before deployment."
                )
            result = await self._logs.query(query)
            if result.ok:
                cols = ", ".join(result.columns or []) or "(none)"
                if result.row_count == 0:
                    return (
                        "OK: the query is valid but returned 0 rows in the last day. "
                        "This is fine if the table has no data yet. "
                        f"Columns: {cols}"
                    )
                return f"OK: {result.row_count} row(s). Columns: {cols}"
            if result.error_kind == "missing_table":
                return (
                    f"ERROR (missing table or column): {result.error}\n"
                    "The table name is wrong, or its DCR has not written any data yet. "
                    "Check the exact custom table name before continuing."
                )
            return f"ERROR ({result.error_kind}): {result.error}"

        return Tool(
            name="run_kql",
            description=(
                "Execute a KQL query against the Log Analytics workspace and report "
                "whether it parsed and how many rows came back. Call this to check "
                "your work BEFORE submitting — it catches syntax errors and proves "
                "the table and columns you referenced actually exist. Append "
                "'| take 1' when you only want to prove validity."
            ),
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "KQL to run."}},
                "required": ["query"],
            },
            fn=_query,
        )

    def _get_table_schema(self) -> Tool:
        async def _schema(table: str) -> str:
            if self._logs is None:
                return (
                    "SKIPPED: no Log Analytics credentials configured, so the schema "
                    "could not be fetched. Ask the analyst for the field list or sample "
                    "data instead."
                )
            result = await self._logs.query(f"{table} | getschema | project ColumnName, ColumnType")
            if not result.ok:
                if result.error_kind == "missing_table":
                    return (
                        f"ERROR (missing table): {result.error}\n"
                        "The table name is wrong, or it hasn't been created yet. "
                        "getschema does not need any rows to have been ingested, but the "
                        "table object itself must exist (created via its DCR, or by a "
                        "first legacy ingest for an HTTP Data Collector API table)."
                    )
                return f"ERROR ({result.error_kind}): {result.error}"
            if not result.rows:
                return f"OK: '{table}' exists but getschema returned no columns."
            lines = [f"{r.get('ColumnName')}: {r.get('ColumnType')}" for r in result.rows]
            return f"OK: {len(lines)} column(s) in '{table}':\n" + "\n".join(lines)

        return Tool(
            name="get_table_schema",
            description=(
                "Fetch the real column list and types for a table that already exists in "
                "the configured Sentinel/Log Analytics workspace, via "
                "'<table> | getschema'. Call this FIRST when the analyst names an "
                "existing table instead of pasting sample data, a schema, or a spec — "
                "it's ground truth and saves them from typing out a field list by hand. "
                "Works even if the table has zero rows so far, as long as it's been "
                "created."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "table": {
                        "type": "string",
                        "description": "Exact table name, e.g. 'Corelight_v2_conn_CL'.",
                    }
                },
                "required": ["table"],
            },
            fn=_schema,
        )

    def _request_input(self) -> Tool:
        return Tool(
            name="request_input",
            description=(
                "Call this INSTEAD of guessing when required information is missing "
                "or ambiguous. State exactly what you need. The analyst will be asked "
                "and the task will be retried with their answer. Never invent field "
                "names, table names, or sample values to work around a gap."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "missing": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "The specific items you are missing.",
                    },
                    "question": {
                        "type": "string",
                        "description": "One clear question to put to the analyst.",
                    },
                },
                "required": ["question"],
            },
            terminal=True,
        )

    # ── stage tool sets ──────────────────────────────────────────────────

    def parser_tools(self) -> list[Tool]:
        submit = Tool(
            name="submit_parser",
            description=(
                "Submit the finished parser. Pass the COMPLETE YAML file content — "
                "id, Function, Category, FunctionName, FunctionAlias, FunctionQuery — "
                "with no markdown fences and no commentary. Call this exactly once, "
                "after you have validated the query with run_kql."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "yaml": {"type": "string", "description": "Complete YAML document."},
                    "notes": {
                        "type": "string",
                        "description": "Optional caveats for the analyst.",
                    },
                },
                "required": ["yaml"],
            },
            terminal=True,
        )
        return [
            self._list_reference(),
            self._read_reference(),
            self._run_python(),
            self._run_kql(),
            self._get_table_schema(),
            self._request_input(),
            submit,
        ]

    def analytic_rule_tools(self) -> list[Tool]:
        submit = Tool(
            name="submit_analytic_rule",
            description=(
                "Submit the finished analytic rule. Pass the COMPLETE YAML file content — "
                "id, name, description, severity, requiredDataConnectors, queryFrequency, "
                "queryPeriod, triggerOperator, triggerThreshold, tactics, techniques, query, "
                "entityMappings, version, kind — with no markdown fences and no commentary. "
                "Call this exactly once, after you have validated the query with run_kql."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "yaml": {"type": "string", "description": "Complete YAML document."},
                    "notes": {
                        "type": "string",
                        "description": "Optional caveats for the analyst.",
                    },
                },
                "required": ["yaml"],
            },
            terminal=True,
        )
        return [
            self._list_reference(),
            self._read_reference(),
            self._run_python(),
            self._run_kql(),
            self._request_input(),
            submit,
        ]

    def tdd_tools(self) -> list[Tool]:
        submit = Tool(
            name="submit_tdd",
            description=(
                "Submit the finished Technical Design Document. Pass the COMPLETE markdown "
                "document — no markdown fences around the whole thing, no commentary before or "
                "after. Call this exactly once."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "markdown": {"type": "string", "description": "Complete TDD markdown document."},
                    "notes": {
                        "type": "string",
                        "description": "Optional caveats for the analyst (e.g. which sections are <TBD>).",
                    },
                },
                "required": ["markdown"],
            },
            terminal=True,
        )
        return [
            self._list_reference(),
            self._read_reference(),
            self._run_python(),
            self._request_input(),
            submit,
        ]

    def manifest_tools(self) -> list[Tool]:
        submit = Tool(
            name="submit_manifest",
            description=(
                "Submit the panel plan for the whole workbook. Plan the panels only — "
                "do NOT write KQL or workbook JSON here; each panel is generated "
                "separately afterwards. Group related panels and order them so the "
                "overview panels come first."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Workbook display name."},
                    "panels": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "title": {"type": "string"},
                                "viz_type": {"type": "string", "enum": VIZ_TYPES},
                                "group": {
                                    "type": "string",
                                    "description": "Section heading, e.g. 'Overview'.",
                                },
                                "tab": {
                                    "type": "string",
                                    "description": "Tab name, or omit for single-tab.",
                                },
                                "width": {
                                    "type": "integer",
                                    "description": "Percent width, 25/33/50/100.",
                                },
                                "intent": {
                                    "type": "string",
                                    "description": "One line: what question it answers.",
                                },
                                "fields": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Parser fields this panel needs.",
                                },
                            },
                            "required": ["id", "title", "viz_type", "group", "intent"],
                        },
                    },
                    "parameters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "field": {"type": "string"},
                                "kind": {"type": "string", "enum": ["multiselect", "text"]},
                            },
                            "required": ["name", "field", "kind"],
                        },
                        "description": "Filters to expose, excluding the time range.",
                    },
                },
                "required": ["title", "panels"],
            },
            terminal=True,
        )
        return [
            self._list_reference(),
            self._read_reference(),
            self._run_python(),
            self._request_input(),
            submit,
        ]

    def panel_tools(self) -> list[Tool]:
        submit = Tool(
            name="submit_panel",
            description=(
                "Submit this one panel. Return the KQL and column labels only — the "
                "workbook JSON, borders, refresh buttons, and parameter wiring are "
                "added for you, so do not write them. Start the query from the parser "
                "function name; the time and parameter filters are prepended for you."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "kql": {
                        "type": "string",
                        "description": (
                            "Query body starting at the parser name. Do not include "
                            "the '| where TimeGenerated {GlobalTimeRestriction}' line."
                        ),
                    },
                    "columns": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "label": {"type": "string"},
                            },
                            "required": ["name", "label"],
                        },
                        "description": "Output columns and their human-readable labels.",
                    },
                    "value_column": {
                        "type": "string",
                        "description": "Numeric column, for tiles and charts.",
                    },
                    "label_column": {
                        "type": "string",
                        "description": "Category/label column, for tiles and charts.",
                    },
                },
                "required": ["id", "title", "kql"],
            },
            terminal=True,
        )
        return [self._run_kql(), submit]
