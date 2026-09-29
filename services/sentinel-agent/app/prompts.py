"""System-prompt assembly.

The two skill files are loaded **verbatim** and are the single source of truth
for generation rules — edit `generate-sentinel-parser.md` and the next run picks
it up, with no rebuild and no second copy to keep in sync.

Only two mechanical adaptations are made, and both are appended as an addendum
rather than edited into the files:

1. The skills tell the model to use `AskUserQuestion`, which does not exist
   here. It is redirected to the `request_input` tool, preserving the rule that
   actually matters — "If anything is missing, stop and ask rather than
   guessing."
2. The workbook skill describes producing a whole dashboard in one pass. Each
   call is scoped to one stage (plan the panels, or write one panel), because
   the assembled artifact is far too large for a single generation.

Prompts are cached in memory but re-read when the file's mtime changes, so
iterating on a skill during development does not need a container restart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

_cache: dict[Path, tuple[float, str]] = {}


def _load(path: Path) -> str:
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"skill file not found at {path}. It is mounted read-only by "
            f"docker-compose; check the volume mapping."
        ) from exc

    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]

    text = path.read_text(encoding="utf-8")
    _cache[path] = (mtime, text)
    return text


_HARNESS_HEAD = """
---

# Harness notes (appended — these override any conflicting instruction above)

You are running as an automated agent, not in an interactive editor.

- **You cannot write files.** Return your work through the submit tool named
  below. Do not wrap it in markdown fences and do not add commentary around it.
"""

# One bullet per optional tool, keyed by the Tool.name it documents. Included
# in the harness only when that tool is actually offered at this stage/by
# this runtime — see harness() below. Without this, a stage or runtime that
# doesn't have e.g. run_kql would still be told it does.
_HARNESS_TOOL_NOTES: dict[str, str] = {
    "request_input": (
        "- **`AskUserQuestion` is not available.** Where the instructions above tell you\n"
        "  to ask the user, call the `request_input` tool instead. The rule itself is\n"
        "  unchanged and is important: when required information is missing or\n"
        "  ambiguous, stop and ask. Never invent field names, table names, or sample\n"
        "  values to work around a gap."
    ),
    "read_reference": (
        "- **Reference files** are read with `read_reference` using paths relative to the\n"
        "  reference directory, one subfolder per skill (e.g. `parser/corelight_conn.yaml`,\n"
        "  `workbook/CorelightDataExplorer.yaml`) — call `list_reference_files` first if\n"
        "  you're not sure of the exact path."
    ),
    "run_python": (
        "- **`run_python`** is available and preferred for schema derivation, counting,\n"
        "  and JSON validation. Use it instead of doing that work token by token."
    ),
    "run_kql": (
        "- **`run_kql`** executes against the real workspace. Use it to check your query\n"
        "  before submitting; it catches syntax errors and proves the table and columns\n"
        "  exist."
    ),
    "get_table_schema": (
        "- **`get_table_schema`** fetches the real column list and types straight from the\n"
        "  configured Sentinel workspace. When the analyst names an existing table instead\n"
        "  of pasting sample data or a schema, call this FIRST rather than asking them to\n"
        "  type out fields you can just look up."
    ),
}


def harness(tool_names: Iterable[str]) -> str:
    """Harness addendum, truthful for exactly the tools passed in."""
    names = set(tool_names)
    bullets = [note for key, note in _HARNESS_TOOL_NOTES.items() if key in names]
    return _HARNESS_HEAD + "\n".join(bullets) + "\n"

_PARSER_STAGE = """
## This request

Produce **one parser** and submit it with `submit_parser`. Follow every rule in
the parser rules section above — `column_ifexists` on every initial-level
`extend`, the `union isfuzzy` + `dummy_table` preamble, suffix-stripped
snake_case names, and a stable final `project`.

Validate the query with `run_kql` before you submit.
"""

_MANIFEST_STAGE = """
## This request — stage 1 of 2: PLAN ONLY

Do **not** write KQL and do **not** write workbook JSON in this stage.

Produce the panel plan and submit it with `submit_manifest`: which panels
should exist, what each is for, its visualisation type, and which group and tab
it belongs to. Each panel is generated individually in stage 2, and the workbook
JSON envelope, groups, parameters, borders, and refresh buttons are assembled
deterministically afterwards — none of that is your job here.

Choose visualisation types using the per-visualisation rules above. Keep the
panel count proportionate to what was actually asked for.
"""

_PANEL_STAGE = """
## This request — stage 2 of 2: ONE PANEL

Write the KQL for the single panel described below and submit it with
`submit_panel`.

Scope, precisely:

- Return the **query body and column labels only.**
- The workbook JSON, `styleSettings`, `showRefreshButton`, `openLastRunQuery`,
  `timeContextFromParameter`, `gridSettings`, `rowLimit`, chart settings and
  legends are all added for you afterwards. Writing them here is wasted work
  and will be discarded.
- **Do not** write the `| where TimeGenerated {GlobalTimeRestriction}` line or
  any `{Parameter}` filter lines — those are prepended for you.
- Start the query at the parser function name.
- Apply the query-shape rules from above that affect the *data*: `top 10` plus
  an "Others" bucket for pie charts, `top-nested` for multi-series time charts,
  `isnotempty()` to drop blanks, and `sort by ts desc` for detail grids.
"""


_ANALYTIC_RULE_STAGE = """
## This request

Produce **one analytic rule** and submit it with `submit_analytic_rule`. Follow every rule in the
rule construction section above — real MITRE tactics/techniques, `requiredDataConnectors` matching the
actual table, no hardcoded values in the query, an explicit final `project`, and at least one entity
mapping whose columns the query actually outputs.

Validate the query with `run_kql` before you submit.
"""


def parser_system(prompts_dir: Path, tool_names: Iterable[str]) -> str:
    skill = _load(Path(prompts_dir) / "generate-sentinel-parser.md")
    return f"{skill}\n{harness(tool_names)}\n{_PARSER_STAGE}"


def analytic_rule_system(prompts_dir: Path, tool_names: Iterable[str]) -> str:
    skill = _load(Path(prompts_dir) / "generate-sentinel-analytic-rule.md")
    return f"{skill}\n{harness(tool_names)}\n{_ANALYTIC_RULE_STAGE}"


_TDD_STAGE = """
## This request

Produce **one Technical Design Document** and submit it with `submit_tdd`. Follow the skeleton and
rules above exactly — keep the `Overall System Architecture` and `Data Connector Architecture`
headings named exactly as shown, include only components actually in scope, and use `<TBD>` rather
than inventing any API/schema detail you weren't given.
"""


def tdd_system(prompts_dir: Path, tool_names: Iterable[str]) -> str:
    skill = _load(Path(prompts_dir) / "generate-sentinel-tdd.md")
    return f"{skill}\n{harness(tool_names)}\n{_TDD_STAGE}"


_CCF_CONNECTOR_STAGE = """
## This request

Produce **one CCF v2 RestApiPoller connector file set** and submit it with
`submit_ccf_connector`. Read the closest-matching reference example first (Step 1 above),
verify the cross-file mapping chain yourself before submitting (Step 8), and remember: no
`Table.json` is needed when the data maps to a standard table.
"""


def ccf_connector_system(prompts_dir: Path, tool_names: Iterable[str]) -> str:
    skill = _load(Path(prompts_dir) / "generate-sentinel-ccf-connector.md")
    return f"{skill}\n{harness(tool_names)}\n{_CCF_CONNECTOR_STAGE}"


def workbook_manifest_system(prompts_dir: Path, tool_names: Iterable[str]) -> str:
    skill = _load(Path(prompts_dir) / "generate-sentinel-workbook.md")
    return f"{skill}\n{harness(tool_names)}\n{_MANIFEST_STAGE}"


def workbook_panel_system(prompts_dir: Path, tool_names: Iterable[str]) -> str:
    skill = _load(Path(prompts_dir) / "generate-sentinel-workbook.md")
    return f"{skill}\n{harness(tool_names)}\n{_PANEL_STAGE}"
