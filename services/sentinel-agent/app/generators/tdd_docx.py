"""Renders a validated TDD markdown draft to `.docx` with embedded draw.io
architecture diagrams.

Reuses the `generate-sentinel-tdd` skill's own kit — `scripts/md_to_docx.py`
plus the two `templates/*.drawio` diagrams — mounted read-only into the
container at `settings.tdd_kit_dir` (see docker-compose.yml). That kit is
already the single source of truth for the interactive Claude Code skill;
this module drives it deterministically instead of asking the model to run
it, since the tool-calling agent here has no shell access.

The filled-in `.drawio` XML only exists to be rendered to a PNG for
embedding in the `.docx` — it isn't a deliverable on its own, so it's built
in a throwaway temp dir (auto-removed once rendered) rather than written to
the persistent `/output` mount. Only `.md` and `.docx` accumulate there.

Blocking and network-bound (renders each diagram via a POST to
convert.diagrams.net) — always call `build_docx` through `asyncio.to_thread`.
"""

from __future__ import annotations

import importlib.util
import io
import logging
import re
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

from ..config import Settings
from ..output import write_binary_artifact

log = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")
_SLUG_RE = re.compile(r"[^A-Za-z0-9]")

# (template filename, anchor heading it belongs after, output suffix,
# always-included). Data Connector Architecture only applies when a data
# connector is in scope — same predicate `check_tdd_structure` uses for the
# anchor-heading check.
_DIAGRAMS = (
    ("overall-architecture.drawio", "Overall System Architecture", "Overall_Architecture", True),
    ("data-connector-architecture.drawio", "Data Connector Architecture", "DataConnector_Architecture", False),
)


def _slug(text: str) -> str:
    return _SLUG_RE.sub("", text) or "Unknown"


def _wants_data_connector(components: list[str]) -> bool:
    return any("connector" in c.lower() for c in components)


def _fill_placeholders(raw: str, values: dict[str, str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), raw)


def _load_converter(kit_dir: Path) -> ModuleType:
    path = kit_dir / "scripts" / "md_to_docx.py"
    spec = importlib.util.spec_from_file_location("tdd_kit_md_to_docx", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load converter module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_docx(
    *,
    settings: Settings,
    solution: str,
    draft_id: str,
    vendor: str,
    product: str,
    components: list[str],
    markdown: str,
    title: str,
) -> dict[str, Any]:
    """Render diagrams, convert `markdown` to `.docx`, write both under the
    solution's output folder. Returns a partial `summary` dict to merge in.

    Never raises: a missing kit, an unreachable render server, or one bad
    diagram degrades to fewer artifacts (and a note in the returned dict)
    rather than failing the whole TDD draft — the markdown has already been
    validated and written by the time this runs.
    """
    result: dict[str, Any] = {}
    kit_dir = settings.tdd_kit_dir

    try:
        converter = _load_converter(kit_dir)
    except Exception as exc:
        log.warning("tdd draft %s: docx converter unavailable: %s", draft_id, exc)
        result["docx_error"] = f"converter unavailable: {exc}"
        return result

    values = {
        "VENDOR": vendor,
        "PRODUCT": product,
        "TABLE": f"{_slug(vendor)}{_slug(product)}_CL",
        "PARSER": f"{_slug(vendor)}{_slug(product)}",
    }

    render_specs: list[tuple[str, bytes, str]] = []
    rendered: list[str] = []
    skipped: list[str] = []

    with tempfile.TemporaryDirectory(prefix=f"tdd-{draft_id}-") as tmp:
        tmp_dir = Path(tmp)
        for template_name, heading, suffix, always in _DIAGRAMS:
            if not always and not _wants_data_connector(components):
                continue
            template_path = kit_dir / "templates" / template_name
            try:
                raw = template_path.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning("tdd draft %s: diagram template missing: %s", draft_id, exc)
                skipped.append(heading)
                continue

            xml = _fill_placeholders(raw, values)
            disk_path = tmp_dir / f"{suffix}.drawio"
            disk_path.write_text(xml, encoding="utf-8")

            try:
                png = converter.render_drawio_to_png(disk_path)
                render_specs.append((heading, png, suffix.replace("_", " ")))
                rendered.append(heading)
            except Exception as exc:
                log.warning("tdd draft %s: diagram render failed for %s: %s", draft_id, heading, exc)
                skipped.append(heading)

    if rendered:
        result["diagrams_rendered"] = rendered
    if skipped:
        result["diagrams_skipped"] = skipped

    try:
        doc = converter.convert(markdown, title=title, diagrams=render_specs, toc=True)
        buf = io.BytesIO()
        doc.save(buf)
        _disk_path, result["docx_file"] = write_binary_artifact(
            settings.output_dir, solution=solution,
            rel_name=f"{draft_id}.docx", content=buf.getvalue(),
        )
    except Exception as exc:
        log.warning("tdd draft %s: docx conversion failed: %s", draft_id, exc)
        result["docx_error"] = str(exc)

    return result
