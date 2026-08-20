"""Tests that need neither a model nor Azure.

The most valuable assertions here are the ones anchored to artifacts whose
correct form is already known: `data/corelight_conn.yaml` and
`data/corelight_intel.yaml` are hand-built parsers, and
`data/CorelightDataExplorer.yaml` is a shipped dashboard. Rules calibrated
against real artifacts are worth more than rules calibrated against opinion.

    pytest tests/ -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "services" / "sentinel-agent"))

from app.lint.rules import lint_parser, lint_workbook  # noqa: E402
from app.workbook import panel_templates as T  # noqa: E402

DATA = REPO / "data"


# ── parser lint ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("filename", ["corelight_conn.yaml", "corelight_intel.yaml"])
def test_known_good_parsers_pass_lint(filename: str) -> None:
    """Calibration: hand-written parsers that ship today must pass cleanly.

    If this fails, the lint is over-strict — fix the rule, not the artifact.
    """
    raw = (DATA / filename).read_text()
    ok, findings = lint_parser(yaml.safe_load(raw), raw)
    errors = [f for f in findings if f.severity == "error"]
    assert ok, f"{filename} should lint clean, got: {[f.message for f in errors]}"


def test_parser_missing_required_keys_is_blocked() -> None:
    raw = "FunctionName: bad\nFunctionQuery: 'Foo_CL | count'\n"
    ok, findings = lint_parser(yaml.safe_load(raw), raw)
    assert not ok
    assert "parser.required_key" in {f.rule for f in findings}


def test_parser_without_column_ifexists_is_blocked() -> None:
    """Rule 3.3 is the one that keeps a parser alive when a column is absent."""
    raw = (
        "id: x\nFunction:\n  Title: T\nCategory: Microsoft Sentinel Parser\n"
        "FunctionName: a_b\nFunctionAlias: a_b\nFunctionQuery: |\n"
        "    let dummy_table = datatable(TimeGenerated: datetime)[];\n"
        "    union isfuzzy=true A_b_CL, dummy_table\n"
        "    | extend ip = ip_s\n"
        "    | project TimeGenerated, ip\n"
    )
    ok, findings = lint_parser(yaml.safe_load(raw), raw)
    assert not ok
    assert "parser.column_ifexists" in {f.rule for f in findings}


def test_parser_without_union_isfuzzy_is_blocked() -> None:
    """Rule 3.5 — without it the parser errors instead of returning empty."""
    raw = (
        "id: x\nFunction:\n  Title: T\nCategory: Microsoft Sentinel Parser\n"
        "FunctionName: a_b\nFunctionAlias: a_b\nFunctionQuery: |\n"
        "    A_b_CL\n"
        "    | extend ip = column_ifexists(\"ip_s\", \"\")\n"
        "    | project TimeGenerated, ip\n"
    )
    ok, findings = lint_parser(yaml.safe_load(raw), raw)
    assert not ok
    rules = {f.rule for f in findings}
    assert {"parser.union_isfuzzy", "parser.dummy_table"} <= rules


def test_parser_with_hardcoded_subscription_id_is_blocked() -> None:
    """Template safety (rule 3.6): nothing environment-specific may be baked in."""
    raw = (DATA / "corelight_conn.yaml").read_text()
    poisoned = raw + (
        "\n# /subscriptions/e0687f99-527c-4ffe-b7d5-d6cb9d563dc2/resourceGroups/x\n"
    )
    ok, findings = lint_parser(yaml.safe_load(raw), poisoned)
    assert not ok
    assert "template.subscription_id" in {f.rule for f in findings}


# ── query composition ───────────────────────────────────────────────────────

PARAMS = [
    {"name": "Sensor", "field": "sensor_name", "kind": "multiselect"},
    {"name": "IPAddress", "field": "src_ip", "kind": "text"},
]


def test_compose_query_prepends_filters_to_a_pipeline_body() -> None:
    out = T.compose_query("corelight_conn", "| summarize Count=count()", PARAMS)
    lines = out.splitlines()
    assert lines[0] == "corelight_conn"
    assert lines[1] == "| where TimeGenerated {GlobalTimeRestriction}"
    assert "('*' in ({Sensor}) or sensor_name in ({Sensor}))" in lines[2]
    assert "('*' == '{IPAddress}' or src_ip == '{IPAddress}')" in lines[3]
    assert lines[-1] == "| summarize Count=count()"


def test_compose_query_inserts_filters_after_a_parser_first_body() -> None:
    """Models vary: some echo the parser name back, some don't. Both must work."""
    out = T.compose_query("corelight_conn", "corelight_conn\n| count", PARAMS)
    assert out.splitlines()[1] == "| where TimeGenerated {GlobalTimeRestriction}"
    assert out.count("corelight_conn") == 1
    assert out.strip().endswith("| count")


def test_substitute_parameters_removes_all_placeholders() -> None:
    """Workbook placeholders are not valid KQL; validation has to expand them."""
    composed = T.compose_query("corelight_conn", "| count", PARAMS)
    expanded = T.substitute_parameters(composed, PARAMS)
    assert "{" not in expanded and "}" not in expanded
    assert "ago(1d)" in expanded


# ── workbook assembly ───────────────────────────────────────────────────────

def _sample_workbook() -> dict:
    query = T.compose_query("corelight_conn", "| summarize Count=count() by service", PARAMS)
    # Skill Step 6 requires pie charts to bound their category count, so the
    # fixture does too — otherwise the lint rightly warns about the legend.
    top_query = T.compose_query(
        "corelight_conn",
        "| summarize Count=count() by service\n| top 10 by Count desc",
        PARAMS,
    )
    panels = [
        T.build_panel(panel_id="p1", title="Total", query=query, viz="tiles",
                      value_column="Count", label_column="service", width=25),
        T.build_panel(panel_id="p2", title="Top Services", query=top_query,
                      viz="piechart", width=50),
        T.build_panel(panel_id="p3", title="Trend", query=query, viz="areachart",
                      value_column="Count", width=50),
        T.build_panel(panel_id="p4", title="Detail", query=query, viz="grid",
                      columns=[{"name": "service", "label": "Service"}], width=100),
    ]
    return T.build_workbook(
        title="Corelight Conn Overview", parser="corelight_conn",
        product="Corelight", topic="Conn",
        parameters=[
            T.global_time_parameter(),
            T.multiselect_parameter("Sensor", "Sensor", "sensor_name", "corelight_conn"),
            T.text_parameter("IPAddress", "IP Address"),
        ],
        body_items=[T.group_item("Overview", panels)],
    )


def test_generated_workbook_passes_lint_with_zero_findings() -> None:
    """Structure comes from templates, so it should be correct by construction.

    A failure here means the templates drifted from the style guide — not that
    a model misbehaved.
    """
    ok, findings = lint_workbook(_sample_workbook(), parser="corelight_conn")
    assert ok, [f"{f.rule}: {f.message}" for f in findings]
    assert findings == []


def test_workbook_assembly_is_byte_deterministic() -> None:
    """Random ids would make every redeploy look like a change and defeat the
    GET-before-PUT diff."""
    assert json.dumps(_sample_workbook()) == json.dumps(_sample_workbook())


def test_panels_are_never_placed_at_the_top_level() -> None:
    """Skill Step 3: every panel lives inside the outer group."""
    types = [item["type"] for item in _sample_workbook()["items"]]
    assert 3 not in types
    assert types == [1, 9, 12, 1]  # header, parameters, group, footer


def test_every_panel_carries_the_mandatory_step_8_fields() -> None:
    wb = _sample_workbook()
    panels = [
        item
        for group in wb["items"]
        if group["type"] == 12
        for sub in group["content"]["items"]
        for item in sub["content"]["items"]
    ]
    assert len(panels) == 4
    for panel in panels:
        content = panel["content"]
        assert content["showRefreshButton"] is True
        assert content["openLastRunQuery"] is True
        assert content["timeContextFromParameter"] == "GlobalTimeRestriction"
        assert content["resourceType"] == "microsoft.operationalinsights/workspaces"
        assert content["styleSettings"]["showBorder"] is True


def test_grid_panels_get_row_limit_and_filter() -> None:
    wb = _sample_workbook()
    grid = next(
        item
        for group in wb["items"] if group["type"] == 12
        for sub in group["content"]["items"]
        for item in sub["content"]["items"]
        if item["content"]["title"] == "Detail"
    )
    assert grid["content"]["gridSettings"]["rowLimit"] == 10000
    assert grid["content"]["gridSettings"]["filter"] is True
    assert grid["content"]["showExportToExcel"] is True


def test_time_chart_pins_timegenerated_on_the_x_axis() -> None:
    """Skill Step 6 is explicit that the axes must not be swapped."""
    wb = _sample_workbook()
    chart = next(
        item
        for group in wb["items"] if group["type"] == 12
        for sub in group["content"]["items"]
        for item in sub["content"]["items"]
        if item["content"].get("visualization") == "areachart"
    )
    assert chart["content"]["chartSettings"]["xAxis"] == "TimeGenerated"


def test_global_time_restriction_has_the_canonical_fifteen_ranges() -> None:
    param = T.global_time_parameter()
    assert len(param["typeSettings"]["selectableValues"]) == 15
    assert param["typeSettings"]["selectableValues"][0]["durationMs"] == 300_000
    assert param["typeSettings"]["selectableValues"][-1]["durationMs"] == 7_776_000_000


def test_workbook_referencing_a_raw_cl_table_is_blocked() -> None:
    """Skill Step 11: panels must go through the parser, never the raw table."""
    bad = T.build_workbook(
        title="Bad", parser="corelight_conn", product="Corelight", topic="Conn",
        parameters=[T.global_time_parameter()],
        body_items=[T.group_item("G", [
            T.build_panel(
                panel_id="x", title="Raw", viz="grid",
                query="Corelight_v2_conn_CL\n| where TimeGenerated {GlobalTimeRestriction}\n| count",
            )
        ])],
    )
    ok, findings = lint_workbook(bad, parser="corelight_conn")
    assert not ok
    assert "workbook.parser_reference" in {f.rule for f in findings}
