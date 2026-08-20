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


_HARNESS = """
---

# Harness notes (appended — these override any conflicting instruction above)

You are running as an automated agent, not in an interactive editor.

- **`AskUserQuestion` is not available.** Where the instructions above tell you
  to ask the user, call the `request_input` tool instead. The rule itself is
  unchanged and is important: when required information is missing or
  ambiguous, stop and ask. Never invent field names, table names, or sample
  values to work around a gap.
- **You cannot write files.** Return your work through the submit tool named
  below. Do not wrap it in markdown fences and do not add commentary around it.
- **Reference files** are read with `read_reference` using paths relative to the
  reference directory (so `corelight_conn.yaml`, not `data/corelight_conn.yaml`).
- **`run_python`** is available and preferred for schema derivation, counting,
  and JSON validation. Use it instead of doing that work token by token.
- **`run_kql`** executes against the real workspace. Use it to check your query
  before submitting; it catches syntax errors and proves the table and columns
  exist.
"""

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


def parser_system(prompts_dir: Path) -> str:
    skill = _load(Path(prompts_dir) / "generate-sentinel-parser.md")
    return f"{skill}\n{_HARNESS}\n{_PARSER_STAGE}"


def workbook_manifest_system(prompts_dir: Path) -> str:
    skill = _load(Path(prompts_dir) / "generate-sentinel-workbook.md")
    return f"{skill}\n{_HARNESS}\n{_MANIFEST_STAGE}"


def workbook_panel_system(prompts_dir: Path) -> str:
    skill = _load(Path(prompts_dir) / "generate-sentinel-workbook.md")
    return f"{skill}\n{_HARNESS}\n{_PANEL_STAGE}"
