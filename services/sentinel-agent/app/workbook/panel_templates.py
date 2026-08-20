"""Deterministic workbook assembly.

Every structural field lives here, extracted from the shipped
`CorelightDataExplorer.yaml` exemplar. The model supplies only a title, a KQL
body, and column labels; everything else — envelope, groups, parameters, tabs,
borders, refresh buttons, grid settings, chart settings — is code.

That split is the reason a small local model can produce a valid workbook at
all: it never has to remember `"resourceType": "microsoft.operationalinsights/
workspaces"`, and it cannot forget it either.

Where the exemplar and the skill disagree, **the skill wins.** The shipped
dashboard predates parts of the style guide — it omits `styleSettings.showBorder`
on most panels and `openLastRunQuery` everywhere, both of which
`generate-sentinel-workbook.md` Step 8 requires on every panel. The templates
apply the rule uniformly.

IDs are `uuid5` over a fixed namespace and the panel's stable id, so
regenerating the same workbook yields byte-identical JSON. Random ids would make
every redeploy look like a change and would defeat the GET-before-PUT diff.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

#: Fixed namespace so ids are reproducible across processes and machines.
_NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

SCHEMA_URL = (
    "https://github.com/Microsoft/Application-Insights-Workbooks/"
    "blob/master/schema/workbook.json"
)
RESOURCE_TYPE = "microsoft.operationalinsights/workspaces"

#: The canonical 15-row time range, 5 minutes to 90 days (skill Step 4).
TIME_RANGE_MS = [
    300_000, 900_000, 1_800_000, 3_600_000, 14_400_000, 43_200_000,
    86_400_000, 172_800_000, 259_200_000, 604_800_000, 1_209_600_000,
    2_419_200_000, 2_592_000_000, 5_184_000_000, 7_776_000_000,
]

#: Number format units used by the workbook renderer.
UNIT_BYTES = 1
UNIT_COUNT = 17

VIZ_SIZES = {"tiles": 3, "piechart": 3, "barchart": 0, "categoricalbar": 0,
             "timechart": 0, "areachart": 0, "grid": 0, "table": 0}


def stable_id(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "|".join(parts)))


# ── query composition ───────────────────────────────────────────────────────

def compose_query(parser: str, body: str, parameters: list[dict[str, Any]]) -> str:
    """Prepend the mandatory time and parameter filters to a model-written body.

    Skill Step 5 fixes this preamble. Doing it here rather than asking the model
    guarantees every panel honours every filter — the single most common defect
    in hand-written workbooks is a panel that quietly ignores one parameter.

    Three body shapes are accepted, because models vary:
      1. starts with ``|``      -> pure pipeline; preamble goes in front
      2. starts with the parser -> filters are inserted after that line
      3. anything else (``let`` preludes) -> preamble prepended before the body
    """
    body = body.strip()
    filters = [f"| where TimeGenerated {{{'GlobalTimeRestriction'}}}"]
    for param in parameters:
        name, field, kind = param["name"], param["field"], param.get("kind", "multiselect")
        if kind == "text":
            filters.append(f"| where ('*' == '{{{name}}}' or {field} == '{{{name}}}')")
        else:
            filters.append(f"| where ('*' in ({{{name}}}) or {field} in ({{{name}}}))")
    filter_block = "\n".join(filters)

    if body.startswith("|"):
        return f"{parser}\n{filter_block}\n{body}"

    lines = body.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == parser or re.match(rf"^\s*{re.escape(parser)}\s*$", line):
            return "\n".join([*lines[: index + 1], filter_block, *lines[index + 1 :]])

    # `let` prelude or an unexpected shape: keep the body intact and put the
    # filtered parser reference in front of it rather than guessing where to cut.
    return f"{parser}\n{filter_block}\n{body}" if parser not in body else body


def substitute_parameters(query: str, parameters: list[dict[str, Any]]) -> str:
    """Replace `{Parameter}` placeholders with their defaults so a panel query
    can actually be executed against Log Analytics.

    Workbook placeholders are not valid KQL — the workbook renderer expands them
    client-side. To validate a panel query for real we have to expand them the
    same way the renderer would at default settings: time range becomes an
    explicit `ago()` window, and every filter becomes its select-all value, which
    makes the guard clause trivially true and leaves the panel's own logic as
    the thing under test.
    """
    expanded = query.replace(
        "| where TimeGenerated {GlobalTimeRestriction}", "| where TimeGenerated > ago(1d)"
    )
    # Remaining bare uses, e.g. inside a `let`.
    expanded = expanded.replace("{GlobalTimeRestriction}", "> ago(1d)")
    expanded = re.sub(
        r"\{GlobalTimeRestriction:(start|end)\}",
        lambda m: "ago(1d)" if m.group(1) == "start" else "now()",
        expanded,
    )
    for param in parameters:
        name = param["name"]
        if param.get("kind") == "text":
            expanded = expanded.replace(f"{{{name}}}", "*")
        else:
            expanded = expanded.replace(f"{{{name}}}", "'*'")
    return expanded


# ── parameters ──────────────────────────────────────────────────────────────

def global_time_parameter(default_ms: int = 86_400_000) -> dict[str, Any]:
    return {
        "id": stable_id("param", "GlobalTimeRestriction"),
        "version": "KqlParameterItem/1.0",
        "name": "GlobalTimeRestriction",
        "label": "Global Time Restriction",
        "type": 4,
        "description": "Select Time Range",
        "isRequired": True,
        "typeSettings": {
            "selectableValues": [{"durationMs": ms} for ms in TIME_RANGE_MS],
            "allowCustom": True,
        },
        "timeContext": {"durationMs": default_ms},
        "value": {"durationMs": default_ms},
    }


def multiselect_parameter(name: str, label: str, field: str, parser: str) -> dict[str, Any]:
    """A dimension filter populated from the parser (skill Step 4, rule 2).

    `isnotempty()` is applied here so blank values never reach the picker.
    """
    return {
        "id": stable_id("param", name),
        "version": "KqlParameterItem/1.0",
        "name": name,
        "label": label,
        "type": 2,
        "isRequired": True,
        "multiSelect": True,
        "quote": "'",
        "delimiter": ",",
        "query": f"{parser}\n| where isnotempty({field})\n| distinct {field}\n| sort by {field} asc",
        "typeSettings": {
            "additionalResourceOptions": ["value::all"],
            "selectAllValue": "*",
            "showDefault": False,
        },
        "timeContext": {"durationMs": 0},
        "timeContextFromParameter": "GlobalTimeRestriction",
        "defaultValue": "value::all",
        "queryType": 0,
        "resourceType": RESOURCE_TYPE,
        "value": ["value::all"],
    }


def text_parameter(name: str, label: str) -> dict[str, Any]:
    """Free-text filter. Skill Step 4 rule 3: these go last, default '*'."""
    return {
        "id": stable_id("param", name),
        "version": "KqlParameterItem/1.0",
        "name": name,
        "label": label,
        "type": 1,
        "isRequired": False,
        "value": "*",
    }


def parameters_item(params: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": 9,
        "content": {
            "version": "KqlParameterItem/1.0",
            "crossComponentResources": ["value::all"],
            "parameters": params,
            "style": "pills",
            "queryType": 0,
            "resourceType": RESOURCE_TYPE,
        },
        "name": "parameters - filters",
    }


# ── structural items ────────────────────────────────────────────────────────

def text_item(markdown: str, name: str) -> dict[str, Any]:
    return {"type": 1, "content": {"json": markdown}, "name": name}


def tabs_item(tabs: list[str]) -> dict[str, Any]:
    """Tab selector (LinkItem). Each link sets the `Tab` parameter, which groups
    then test with conditionalVisibility."""
    return {
        "type": 11,
        "content": {
            "version": "LinkItem/1.0",
            "style": "tabs",
            "links": [
                {
                    "id": stable_id("tab", tab),
                    "cellValue": "Tab",
                    "linkTarget": "parameter",
                    "linkLabel": tab,
                    "subTarget": slugify(tab),
                    "style": "link",
                }
                for tab in tabs
            ],
        },
        "name": "tabs",
    }


def group_item(
    title: str, items: list[dict[str, Any]], *, tab: str | None = None, name: str | None = None
) -> dict[str, Any]:
    group: dict[str, Any] = {
        "type": 12,
        "content": {
            "version": "NotebookGroup/1.0",
            "groupType": "editable",
            "title": title,
            "items": items,
        },
        "name": name or f"group_{slugify(title)}",
    }
    if tab:
        group["conditionalVisibility"] = {
            "parameterName": "Tab",
            "comparison": "isEqualTo",
            "value": slugify(tab),
        }
    return group


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


# ── panels ──────────────────────────────────────────────────────────────────

def _base_content(title: str, query: str, viz: str) -> dict[str, Any]:
    """Fields skill Step 8 requires on every panel, without exception."""
    return {
        "version": "KqlItem/1.0",
        "query": query,
        "size": VIZ_SIZES.get(viz, 0),
        "title": title,
        "showAnalytics": True,
        "noDataMessage": "No data found.",
        "showRefreshButton": True,
        "openLastRunQuery": True,
        "timeContextFromParameter": "GlobalTimeRestriction",
        "queryType": 0,
        "resourceType": RESOURCE_TYPE,
        # Skill Step 8: every panel gets a border. The shipped exemplar omits
        # this on most panels; the style guide is authoritative.
        "styleSettings": {"showBorder": True},
    }


def _label_settings(columns: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {"columnId": c["name"], "label": c.get("label") or c["name"]}
        for c in columns
        if c.get("name")
    ]


def build_panel(
    *,
    panel_id: str,
    title: str,
    query: str,
    viz: str,
    columns: list[dict[str, str]] | None = None,
    value_column: str | None = None,
    label_column: str | None = None,
    width: int | None = None,
    tab: str | None = None,
    export_field: str | None = None,
    export_parameter: str | None = None,
) -> dict[str, Any]:
    """Assemble one `type: 3` panel with all per-visualisation rules applied."""
    columns = columns or []
    content = _base_content(title, query, viz)

    if viz == "tiles":
        # Skill Step 6: value centred, compact units, palette, border on.
        content["visualization"] = "tiles"
        content["tileSettings"] = {
            "titleContent": {"columnMatch": label_column or "Category", "formatter": 1},
            "leftContent": {
                "columnMatch": value_column or "Count",
                "formatter": 12,
                "formatOptions": {"palette": "auto"},
                "numberFormat": {
                    "unit": UNIT_COUNT,
                    "options": {"style": "decimal", "maximumFractionDigits": 1,
                                "useGrouping": True, "notation": "compact"},
                },
            },
            "showBorder": True,
        }

    elif viz == "piechart":
        content["visualization"] = "piechart"
        content["chartSettings"] = {
            "showLegend": True,
            "createOtherGroup": 10,
            "ySettings": {"numberFormatSettings": {"unit": UNIT_COUNT,
                                                   "options": {"style": "decimal"}}},
        }

    elif viz in ("barchart", "categoricalbar"):
        content["visualization"] = "categoricalbar"
        content["chartSettings"] = {
            "showLegend": True,
            "createOtherGroup": 10,
            "showDataPoints": True,
            "xAxis": label_column or "Category",
            "yAxis": [value_column or "Count"],
            "ySettings": {"numberFormatSettings": {"unit": UNIT_COUNT,
                                                   "options": {"style": "decimal"}}},
        }

    elif viz in ("timechart", "areachart"):
        # Skill Step 6: TimeGenerated on X, metric on Y — never swapped.
        content["visualization"] = viz
        content["chartSettings"] = {
            "showLegend": True,
            "createOtherGroup": 10,
            "xAxis": "TimeGenerated",
            "yAxis": [value_column] if value_column else [],
            "ySettings": {"numberFormatSettings": {"unit": UNIT_COUNT,
                                                   "options": {"style": "decimal"}}},
        }

    else:  # grid / table — the default rendering
        if viz == "table":
            content["visualization"] = "table"
        content["showExportToExcel"] = True
        grid: dict[str, Any] = {"rowLimit": 10000, "filter": True}
        if columns:
            grid["labelSettings"] = _label_settings(columns)
        content["gridSettings"] = grid

    if export_field:
        content["exportFieldName"] = export_field
        content["exportParameterName"] = export_parameter or f"Selected_{export_field}"
        content["exportDefaultValue"] = "none"

    item: dict[str, Any] = {
        "type": 3,
        "content": content,
        "name": f"query - {slugify(panel_id)}",
    }
    if width:
        item["customWidth"] = str(width)
    if tab:
        item["conditionalVisibility"] = {
            "parameterName": "Tab",
            "comparison": "isEqualTo",
            "value": slugify(tab),
        }
    return item


# ── envelope ────────────────────────────────────────────────────────────────

def build_workbook(
    *,
    title: str,
    parser: str,
    product: str,
    topic: str,
    parameters: list[dict[str, Any]],
    body_items: list[dict[str, Any]],
    tabs: list[str] | None = None,
) -> dict[str, Any]:
    """The outer skeleton from skill Step 3.

    Panels are never placed at the top level; they always sit inside the single
    outer `type: 12` group, which is what the skill requires.
    """
    header = text_item(
        f"# {title}\n---\n"
        f">**NOTE:** This workbook depends on the **{parser}** parser "
        f"(a Kusto function) being deployed in the workspace.",
        "text - header",
    )

    items: list[dict[str, Any]] = [header, parameters_item(parameters)]
    if tabs:
        items.append(tabs_item(tabs))
    items.append(group_item(title, body_items, name="group_main"))
    items.append(
        text_item(
            "Refresh the web page to fetch details of recently collected events",
            "text - footer",
        )
    )

    return {
        "version": "Notebook/1.0",
        "items": items,
        "fromTemplateId": f"sentinel-{product}_{topic}_Dashboard",
        "$schema": SCHEMA_URL,
    }
