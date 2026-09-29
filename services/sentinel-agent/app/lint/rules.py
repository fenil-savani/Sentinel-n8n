"""Structural lint — the skills' rules, enforced mechanically.

Both skill files contain rules a prompt can only *ask* for. This module checks
them instead, so a rule holds regardless of which model ran or how well it was
having a day.

Findings are graded:

    error   blocks deployment
    warn    surfaced on the confirm turn, does not block

Most workbook rules should pass by construction, because `panel_templates.py`
generates the structure. They are checked anyway to catch template drift — if a
rule starts failing, the template regressed, not the model.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

# Template-safety patterns (workbook skill Step 11 / parser rule 3.6).
_SUBSCRIPTION_ID = re.compile(r"/subscriptions/[0-9a-fA-F-]{36}", re.IGNORECASE)
_TENANT_GUID = re.compile(r"\btenant[_-]?id\b\s*[:=]\s*['\"]?[0-9a-fA-F-]{36}", re.IGNORECASE)
_HTTP_URL = re.compile(r"https?://(?!aka\.ms|learn\.microsoft\.com|github\.com)[^\s\"']+")
_RAW_CL_TABLE = re.compile(r"\b([A-Za-z0-9_]+_CL)\b")


@dataclass(slots=True)
class Finding:
    rule: str
    severity: str  # "error" | "warn"
    message: str
    where: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ok(findings: list[Finding]) -> bool:
    return not any(f.severity == "error" for f in findings)


# ── parser ──────────────────────────────────────────────────────────────────

REQUIRED_PARSER_KEYS = ("id", "Function", "Category", "FunctionName",
                        "FunctionAlias", "FunctionQuery")


def lint_parser(doc: dict[str, Any], raw: str) -> tuple[bool, list[Finding]]:
    findings: list[Finding] = []

    for key in REQUIRED_PARSER_KEYS:
        if key not in doc:
            findings.append(Finding("parser.required_key", "error", f"missing top-level key '{key}'"))

    query = str(doc.get("FunctionQuery") or "")
    alias = str(doc.get("FunctionAlias") or "")
    name = str(doc.get("FunctionName") or "")

    # Rule 3.5 — union with an empty table so the function returns empty rather
    # than erroring when the custom table does not exist yet.
    if "union isfuzzy=true" not in query.replace(" ", " "):
        findings.append(
            Finding("parser.union_isfuzzy", "error",
                    "FunctionQuery must start from 'union isfuzzy=true <Table>_CL, dummy_table' "
                    "so the parser returns empty instead of failing when the table is absent")
        )
    if "dummy_table" not in query:
        findings.append(
            Finding("parser.dummy_table", "error",
                    "no dummy_table datatable declared; it must carry at least "
                    "TimeGenerated and the dedup key column")
        )

    # Rule 3.3 — column_ifexists on every initial-level extend.
    if "column_ifexists" not in query:
        findings.append(
            Finding("parser.column_ifexists", "error",
                    "no column_ifexists() found; every initial-level extend must use it "
                    "so the parser survives a missing column")
        )
    else:
        bare = _bare_extends(query)
        if bare:
            findings.append(
                Finding("parser.column_ifexists", "error",
                        "these initial-level extends assign a raw column without "
                        f"column_ifexists(): {', '.join(sorted(bare)[:8])}")
            )

    if "| project" not in query:
        findings.append(
            Finding("parser.final_project", "warn",
                    "no final '| project' — downstream queries break when upstream "
                    "column order changes")
        )

    # Naming: snake_case, lowercase, and the two names agree.
    if alias and not re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)*", alias):
        findings.append(
            Finding("parser.naming", "error",
                    f"FunctionAlias '{alias}' must be lowercase snake_case")
        )
    if alias and name and alias != name:
        findings.append(
            Finding("parser.naming", "warn",
                    f"FunctionName '{name}' and FunctionAlias '{alias}' differ; "
                    "the skill requires both to be <product>_<logtype>")
        )

    findings.extend(_template_safety(raw, "FunctionQuery"))
    return _ok(findings), findings


def _bare_extends(query: str) -> set[str]:
    """Find `alias = raw_col_s` assignments that skipped column_ifexists.

    Only the *initial* extend block is in scope: rule 3.3 says "at initial level
    only", and later extends legitimately derive from already-normalised names.
    The initial block is the one whose right-hand sides carry Sentinel column
    suffixes (_s/_d/_t/_b/_g).
    """
    offenders: set[str] = set()
    for line in query.splitlines():
        stripped = line.strip().rstrip(",")
        match = re.fullmatch(
            r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([A-Za-z_][A-Za-z0-9_]*_(?:s|d|t|b|g))", stripped
        )
        if match and "column_ifexists" not in stripped:
            offenders.add(match.group(1))
    return offenders


# ── analytic rule ───────────────────────────────────────────────────────────

REQUIRED_RULE_KEYS = ("id", "name", "description", "severity", "requiredDataConnectors",
                      "queryFrequency", "queryPeriod", "triggerOperator", "triggerThreshold",
                      "tactics", "techniques", "query", "entityMappings", "version", "kind")

_VALID_SEVERITIES = {"Informational", "Low", "Medium", "High"}
_VALID_TACTICS = {
    "InitialAccess", "Execution", "Persistence", "PrivilegeEscalation", "DefenseEvasion",
    "CredentialAccess", "Discovery", "LateralMovement", "Collection", "CommandAndControl",
    "Exfiltration", "Impact",
}
_TECHNIQUE_ID = re.compile(r"^T\d{4}(\.\d{3})?$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def lint_analytic_rule(doc: dict[str, Any], raw: str) -> tuple[bool, list[Finding]]:
    findings: list[Finding] = []

    for key in REQUIRED_RULE_KEYS:
        if key not in doc:
            findings.append(Finding("rule.required_key", "error", f"missing top-level key '{key}'"))

    rule_id = str(doc.get("id") or "")
    if rule_id and not _UUID.match(rule_id):
        findings.append(Finding("rule.id", "error", f"'id' is not a valid UUID: {rule_id!r}"))

    if doc.get("kind") != "Scheduled":
        findings.append(Finding("rule.kind", "error",
                                "'kind' must be 'Scheduled' — NRT/Fusion rules are out of scope"))

    severity = str(doc.get("severity") or "")
    if severity not in _VALID_SEVERITIES:
        findings.append(Finding("rule.severity", "error",
                                f"'severity' must be one of {sorted(_VALID_SEVERITIES)}, got {severity!r}"))

    tactics = doc.get("tactics") or []
    if not tactics:
        findings.append(Finding("rule.tactics", "error", "'tactics' must not be empty"))
    else:
        bad = [t for t in tactics if t not in _VALID_TACTICS]
        if bad:
            findings.append(Finding("rule.tactics", "error",
                                    f"not valid MITRE tactics: {bad}"))

    techniques = doc.get("techniques") or []
    if not techniques:
        findings.append(Finding("rule.techniques", "error", "'techniques' must not be empty"))
    else:
        bad_t = [t for t in techniques if not _TECHNIQUE_ID.match(str(t))]
        if bad_t:
            findings.append(Finding("rule.techniques", "error",
                                    f"not valid MITRE technique ids (expect Txxxx or Txxxx.xxx): {bad_t}"))

    connectors = doc.get("requiredDataConnectors") or []
    if not connectors:
        findings.append(Finding("rule.connectors", "error", "'requiredDataConnectors' must not be empty"))
    else:
        for conn in connectors:
            if not conn.get("connectorId"):
                findings.append(Finding("rule.connectors", "error",
                                        "a requiredDataConnectors entry is missing 'connectorId'"))
            if not conn.get("dataTypes"):
                findings.append(Finding("rule.connectors", "error",
                                        "a requiredDataConnectors entry is missing 'dataTypes'"))

    entity_mappings = doc.get("entityMappings") or []
    if not entity_mappings:
        findings.append(Finding("rule.entities", "error", "'entityMappings' must have at least one entry"))

    query = str(doc.get("query") or "")
    if not query.strip():
        findings.append(Finding("rule.query", "error", "'query' is empty"))
    else:
        if "| project" not in query:
            findings.append(Finding("rule.final_project", "warn",
                                    "no final '| project' — downstream entity mappings can break "
                                    "when upstream column order changes"))
        mapped_columns = {
            fm.get("columnName")
            for entry in entity_mappings
            for fm in (entry.get("fieldMappings") or [])
            if fm.get("columnName")
        }
        missing_cols = [c for c in mapped_columns if c and c not in query]
        if missing_cols:
            findings.append(Finding("rule.entity_columns", "error",
                                    f"entityMappings reference column(s) not found in the query: "
                                    f"{sorted(missing_cols)}"))

    findings.extend(_template_safety(raw, "query"))
    return _ok(findings), findings


# ── workbook ────────────────────────────────────────────────────────────────

def lint_workbook(
    wb: dict[str, Any], *, parser: str | list[str] | None = None
) -> tuple[bool, list[Finding]]:
    """`parser` is a single name in the common case, or a list when the
    workbook spans multiple parsers (see WorkbookRequest.parsers) — a panel
    passes Step 11 if it references ANY of them."""
    findings: list[Finding] = []
    valid_parsers = {parser} if isinstance(parser, str) else set(parser or ())

    if wb.get("version") != "Notebook/1.0":
        findings.append(Finding("workbook.version", "error",
                                "top-level version must be 'Notebook/1.0'"))
    if "$schema" not in wb:
        findings.append(Finding("workbook.schema", "warn", "missing $schema"))

    items = wb.get("items") or []
    if not items:
        findings.append(Finding("workbook.items", "error", "workbook has no items"))
        return False, findings

    # Step 3 — panels live inside a group, never bare at the top level.
    for item in items:
        if item.get("type") == 3:
            findings.append(
                Finding("workbook.grouping", "error",
                        f"panel '{item.get('name')}' sits at the top level; every panel "
                        "must be inside a type-12 group")
            )

    if not any(i.get("type") == 9 for i in items):
        findings.append(Finding("workbook.parameters", "error",
                                "no parameters block (type 9) — GlobalTimeRestriction is required"))
    else:
        findings.extend(_check_time_parameter(items))

    panels = list(_walk_panels(items))
    if not panels:
        findings.append(Finding("workbook.panels", "error", "no query panels found"))

    for panel in panels:
        findings.extend(_check_panel(panel, valid_parsers))

    findings.extend(_template_safety(_stringify(wb), "workbook"))
    return _ok(findings), findings


def _walk_panels(items: list[dict[str, Any]]):
    for item in items:
        content = item.get("content") or {}
        if item.get("type") == 3:
            yield item
        if content.get("items"):
            yield from _walk_panels(content["items"])


def _check_time_parameter(items: list[dict[str, Any]]) -> list[Finding]:
    for item in items:
        if item.get("type") != 9:
            continue
        for param in (item.get("content") or {}).get("parameters", []):
            if param.get("name") == "GlobalTimeRestriction":
                values = (param.get("typeSettings") or {}).get("selectableValues") or []
                if len(values) != 15:
                    return [
                        Finding("workbook.time_range", "warn",
                                f"GlobalTimeRestriction has {len(values)} selectable values; "
                                "the canonical list is 15 (5 min to 90 days)")
                    ]
                return []
    return [Finding("workbook.time_range", "error",
                    "no GlobalTimeRestriction parameter defined")]


_REQUIRED_PANEL_FIELDS = {
    "showRefreshButton": True,
    "openLastRunQuery": True,
    "timeContextFromParameter": "GlobalTimeRestriction",
    "queryType": 0,
    "resourceType": "microsoft.operationalinsights/workspaces",
}


def _check_panel(item: dict[str, Any], valid_parsers: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    content = item.get("content") or {}
    where = content.get("title") or item.get("name") or "<unnamed panel>"
    viz = content.get("visualization", "grid")

    for field, expected in _REQUIRED_PANEL_FIELDS.items():
        if content.get(field) != expected:
            findings.append(
                Finding("workbook.panel_fields", "error",
                        f"'{field}' must be {expected!r} (Step 8)", where)
            )

    if not (content.get("styleSettings") or {}).get("showBorder"):
        findings.append(Finding("workbook.border", "error",
                                "styleSettings.showBorder must be true", where))
    if not content.get("noDataMessage"):
        findings.append(Finding("workbook.no_data_message", "warn",
                                "missing noDataMessage", where))

    query = content.get("query") or ""
    if not query.strip():
        findings.append(Finding("workbook.query", "error", "panel has no query", where))
        return findings

    # Skill Step 5 wants the explicit `| where TimeGenerated {GlobalTimeRestriction}`
    # line, which is what our generator emits. Binding time solely through
    # `timeContextFromParameter` is still valid Sentinel, though — so that is a
    # warning, and only a panel with neither mechanism is actually unfiltered.
    if "{GlobalTimeRestriction}" not in query:
        bound_via_context = (
            content.get("timeContextFromParameter") == "GlobalTimeRestriction"
        )
        findings.append(
            Finding(
                "workbook.time_filter",
                "warn" if bound_via_context else "error",
                "query has no explicit '| where TimeGenerated {GlobalTimeRestriction}' "
                + (
                    "(time is still bound via timeContextFromParameter)"
                    if bound_via_context
                    else "and no timeContextFromParameter — the panel ignores the time range"
                ),
                where,
            )
        )

    # Step 11 — panels must go through a parser, not the raw custom table.
    raw_tables = set(_RAW_CL_TABLE.findall(query))
    if raw_tables and valid_parsers and not any(p in query for p in valid_parsers):
        findings.append(
            Finding("workbook.parser_reference", "error",
                    f"query hits raw table(s) {', '.join(sorted(raw_tables))} instead of "
                    f"one of the parsers {sorted(valid_parsers)}", where)
        )

    if viz in ("grid", "table"):
        grid = content.get("gridSettings") or {}
        if not grid.get("filter"):
            findings.append(Finding("workbook.grid_filter", "error",
                                    "gridSettings.filter must be true (Step 6)", where))
        if grid.get("rowLimit") != 10000:
            findings.append(Finding("workbook.row_limit", "error",
                                    "gridSettings.rowLimit must be 10000 (Step 6)", where))
        if not content.get("showExportToExcel"):
            findings.append(Finding("workbook.export", "warn",
                                    "showExportToExcel should be true", where))
    elif viz in ("piechart", "barchart", "categoricalbar", "timechart", "areachart"):
        chart = content.get("chartSettings") or {}
        if not chart.get("showLegend"):
            findings.append(Finding("workbook.legend", "error",
                                    "chartSettings.showLegend must be true (Step 6)", where))
        if viz in ("timechart", "areachart"):
            x_axis = chart.get("xAxis")
            if x_axis is None:
                # Absent means the renderer infers it, which usually works.
                findings.append(
                    Finding("workbook.axes", "warn",
                            "time chart does not pin xAxis; set it to TimeGenerated "
                            "so the axes cannot be inferred the wrong way round", where)
                )
            elif x_axis != "TimeGenerated":
                findings.append(
                    Finding("workbook.axes", "error",
                            f"time chart has xAxis '{x_axis}'; it must be TimeGenerated "
                            "(Step 6 — do not swap the axes)", where)
                )
    elif viz == "tiles":
        tiles = content.get("tileSettings") or {}
        if not tiles.get("showBorder"):
            findings.append(Finding("workbook.tile_border", "error",
                                    "tileSettings.showBorder must be true (Step 6)", where))
        left = (tiles.get("leftContent") or {}).get("numberFormat") or {}
        if left.get("unit") not in (1, 17):
            findings.append(
                Finding("workbook.tile_format", "warn",
                        "tile value should set numberFormat.unit (17 count / 1 bytes) so "
                        "raw decimals are never displayed", where)
            )

    # Step 9 — pie charts must bound their category count.
    if viz == "piechart" and not re.search(r"\|\s*top\s+\d+", query, re.IGNORECASE):
        findings.append(
            Finding("workbook.chart_limits", "warn",
                    "pie chart query has no 'top N' — the legend will be unreadable "
                    "if the field is high-cardinality", where)
        )

    return findings


# ── ccf connector ───────────────────────────────────────────────────────────
# Deterministic port of the reviewer's 18 auto-fail conditions and the
# cross-file mapping chain (see generate-sentinel-ccf-connector/SKILL.md
# Step 8) — checked mechanically rather than by a second LLM pass, same
# philosophy as every other lint_* in this file.

_RESERVED_KQL_COLUMNS = {"type", "count", "title", "id", "status", "class", "level", "timestamp"}
_RESERVED_OAUTH2_PARAMS = {
    "grant_type", "client_id", "client_secret", "code", "redirect_uri", "scope", "apikey",
}
# Auth fields expected to hold a {{placeholder}} or ARM expression, never a literal secret.
_SECRET_AUTH_FIELDS = ("ApiKey", "Password", "ClientSecret", "UserToken", "AuthorizationCode")


def _looks_like_placeholder(value: Any) -> bool:
    text = str(value)
    return text.startswith("{{") or text.startswith("[") or text.startswith("{_")


def lint_ccf_connector(
    connector_definition: dict[str, Any],
    poller_config: list[dict[str, Any]],
    dcr: list[dict[str, Any]],
    table: list[dict[str, Any]] | None,
    raw: str,
) -> tuple[bool, list[Finding]]:
    findings: list[Finding] = []

    cd_props = (connector_definition.get("properties") or {}).get("connectorUiConfig") or {}
    cd_name = connector_definition.get("name")
    cd_id = cd_props.get("id")

    # Gate 2 / auto-fail #4 — name and connectorUiConfig.id identical.
    if cd_name != cd_id:
        findings.append(Finding("ccf.connector_id", "error",
                                f"ConnectorDefinition 'name' ({cd_name!r}) and "
                                f"'connectorUiConfig.id' ({cd_id!r}) must be identical"))
    if len(cd_props.get("sampleQueries") or []) < 2:
        findings.append(Finding("ccf.sample_queries", "error",
                                "connectorUiConfig.sampleQueries must have at least 2 entries"))
    if connector_definition.get("kind") != "Customizable":
        findings.append(Finding("ccf.connector_kind", "error",
                                "ConnectorDefinition 'kind' must be 'Customizable'"))
    # Auto-fail #15 — CLv1 detection: a real CCF v2 file never carries pollerConfig/auth
    # alongside connectorUiConfig in the same document.
    if "pollerConfig" in connector_definition or "auth" in connector_definition:
        findings.append(Finding("ccf.clv1_pattern", "error",
                                "ConnectorDefinition appears to be CLv1 (contains pollerConfig/auth "
                                "in the same file) — CLv2 requires separate files"))

    # Auto-fail #18 — duplicate poller names.
    names = [p.get("name") for p in poller_config]
    dupes = {n for n in names if n and names.count(n) > 1}
    if dupes:
        findings.append(Finding("ccf.poller_names", "error",
                                f"duplicate poller name(s) in PollerConfig array: {sorted(dupes)}"))

    dcr_stream_keys = set()
    for entry in dcr:
        dcr_stream_keys.update((entry.get("properties") or {}).get("streamDeclarations") or {})

    table_names = set()
    if table:
        for t in table:
            t_name = t.get("name")
            schema_name = ((t.get("properties") or {}).get("schema") or {}).get("name")
            # Auto-fail #5 / #6 — Table naming.
            if t_name != schema_name:
                findings.append(Finding("ccf.table_name", "error",
                                        f"Table 'name' ({t_name!r}) and 'schema.name' ({schema_name!r}) "
                                        "must be identical"))
            for candidate in (t_name, schema_name):
                if candidate and "Custom-" in candidate:
                    findings.append(Finding("ccf.table_custom_prefix", "error",
                                            f"Table name must NOT contain 'Custom-': {candidate!r}"))
                if candidate and not candidate.endswith("_CL"):
                    findings.append(Finding("ccf.table_suffix", "error",
                                            f"Table name must end in '_CL': {candidate!r}"))
            if t_name:
                table_names.add(t_name)

            columns = ((t.get("properties") or {}).get("schema") or {}).get("columns") or []
            col_names = {c.get("name") for c in columns if c.get("name")}
            if "TimeGenerated" not in col_names:
                findings.append(Finding("ccf.table_time_generated", "error",
                                        "Table schema is missing a 'TimeGenerated' column"))
            if "TenantId" in col_names:
                findings.append(Finding("ccf.table_tenant_id", "error",
                                        "Table schema must NOT declare 'TenantId' — Azure adds it automatically"))
            bad_cols = col_names & _RESERVED_KQL_COLUMNS
            if bad_cols:
                findings.append(Finding("ccf.reserved_column", "error",
                                        f"Table columns use reserved KQL keywords without renaming: {sorted(bad_cols)}"))

    for poller in poller_config:
        p = poller.get("properties") or {}
        where = poller.get("name") or "<unnamed poller>"

        # Auto-fail #3 — connectorDefinitionName must match ConnectorDefinition id.
        if p.get("connectorDefinitionName") != cd_id:
            findings.append(Finding("ccf.connector_ref", "error",
                                    f"connectorDefinitionName ({p.get('connectorDefinitionName')!r}) does not "
                                    f"match ConnectorDefinition id ({cd_id!r})", where))

        stream_name = (p.get("dcrConfig") or {}).get("streamName")
        # Auto-fail #7 — streamName must start with Custom-.
        if stream_name and not stream_name.startswith("Custom-"):
            findings.append(Finding("ccf.stream_prefix", "error",
                                    f"dcrConfig.streamName must start with 'Custom-': {stream_name!r}", where))
        # Auto-fail #2 — streamName must exist as a DCR streamDeclarations key.
        if stream_name and stream_name not in dcr_stream_keys:
            findings.append(Finding("ccf.stream_mismatch", "error",
                                    f"dcrConfig.streamName {stream_name!r} has no matching "
                                    f"streamDeclarations key in the DCR (found: {sorted(dcr_stream_keys)})", where))

        auth = p.get("auth") or {}
        auth_type = auth.get("type")
        if auth_type == "OAuth2":
            reserved_hit = _RESERVED_OAUTH2_PARAMS & set(auth.get("TokenEndpointQueryParameters") or {})
            # Auto-fail #11.
            if reserved_hit:
                findings.append(Finding("ccf.oauth2_reserved_param", "error",
                                        f"TokenEndpointQueryParameters contains reserved param(s): "
                                        f"{sorted(reserved_hit)} — this fails with 'BadRequest: OAuth2 config error'",
                                        where))
        for field in _SECRET_AUTH_FIELDS:
            value = auth.get(field)
            if value is not None and not _looks_like_placeholder(value):
                findings.append(Finding("ccf.hardcoded_secret", "error",
                                        f"auth.{field} does not look like a {{{{placeholder}}}} or ARM "
                                        f"expression — looks like a hardcoded literal", where))

        # Auto-fail #13.
        if p.get("logResponseContent") is True:
            findings.append(Finding("ccf.log_response_content", "error",
                                    "logResponseContent must not be true in production output", where))

    for entry in dcr:
        dp = entry.get("properties") or {}
        dcr_name = entry.get("name") or ""
        # Auto-fail #9 — DCR name length.
        if len(dcr_name) > 65:
            findings.append(Finding("ccf.dcr_name_length", "error",
                                    f"DCR name is {len(dcr_name)} characters (max 65): {dcr_name!r}"))
        if " " in dcr_name:
            findings.append(Finding("ccf.dcr_name_spaces", "error",
                                    f"DCR name must not contain spaces: {dcr_name!r}"))

        for flow in dp.get("dataFlows") or []:
            streams = flow.get("streams") or []
            where = f"dataFlows[streams={streams}]"
            # Auto-fail #16 — exactly one stream per dataFlows entry.
            if len(streams) != 1:
                findings.append(Finding("ccf.dataflow_streams", "error",
                                        f"dataFlows entry must have exactly ONE stream, found {len(streams)}",
                                        where))
            transform = str(flow.get("transformKql") or "")
            # Auto-fail #1 — TimeGenerated must be set.
            if "TimeGenerated" not in transform:
                findings.append(Finding("ccf.time_generated", "error",
                                        "transformKql does not set TimeGenerated", where))

            output_stream = flow.get("outputStream")
            if table_names:
                # Auto-fail #17 — outputStream required and must match "Custom-" + table name.
                expected = {f"Custom-{t}" for t in table_names}
                if output_stream not in expected:
                    findings.append(Finding("ccf.output_stream", "error",
                                            f"outputStream ({output_stream!r}) does not match any "
                                            f"custom table as 'Custom-<TableName>' (expected one of {sorted(expected)})",
                                            where))
                elif output_stream:
                    table_col_names: set[str] = set()
                    for t in table or []:
                        if f"Custom-{t.get('name')}" == output_stream:
                            cols = ((t.get("properties") or {}).get("schema") or {}).get("columns") or []
                            table_col_names = {c.get("name") for c in cols if c.get("name")}
                    # Auto-fail #8 (heuristic) — every declared output column should be
                    # traceable in the transform (as an extend/rename target or an
                    # untouched passthrough field). Flag anything that never appears.
                    missing = {
                        c for c in table_col_names
                        if c not in ("TimeGenerated",) and not re.search(rf"\b{re.escape(c)}\b", transform)
                    }
                    if missing:
                        findings.append(Finding("ccf.kql_table_mismatch", "error",
                                                f"Table column(s) never referenced in transformKql: {sorted(missing)}",
                                                where))

    findings.extend(_template_safety(raw, "ccf_connector"))
    return _ok(findings), findings


# ── shared ──────────────────────────────────────────────────────────────────

def _stringify(value: Any) -> str:
    import json

    return json.dumps(value)


def _template_safety(blob: str, where: str) -> list[Finding]:
    """Skill Step 11 / parser rule 3.6 — nothing environment-specific may be
    baked into an artifact that is meant to be redeployable."""
    findings: list[Finding] = []
    if _SUBSCRIPTION_ID.search(blob):
        findings.append(
            Finding("template.subscription_id", "error",
                    "contains a hardcoded /subscriptions/<guid> resource id", where)
        )
    if _TENANT_GUID.search(blob):
        findings.append(
            Finding("template.tenant_id", "error", "contains a hardcoded tenant id", where)
        )
    for url in set(_HTTP_URL.findall(blob)):
        findings.append(
            Finding("template.hardcoded_url", "warn",
                    f"contains a hardcoded URL: {url[:80]}", where)
        )
    return findings
