"""Writes generated artifacts to the shared output folder.

Postgres remains the source of truth for status/lint/deploy — this exists
purely so the analyst can open, read, and review a draft with their own
tools instead of only ever seeing a summary. A write here is best-effort
visibility, not the persistence layer, so callers should treat a failure as
non-fatal to generation.

Layout mirrors a real Azure-Sentinel solution repo
(``<Solution>/Parsers/<name>.yaml``, ``<Solution>/Analytic Rules/<name>.yaml``,
...) since that's the structure the adapted skills already assume.
"""

from __future__ import annotations

import re
from pathlib import Path

_UNSAFE = re.compile(r"[^A-Za-z0-9 _.-]")

# One path template per kind, relative to the solution's own folder. `{name}`
# is filled with the sanitized artifact name. Anything not listed here falls
# back to a generic `<Kind>/<name>.txt` — deliberately permissive so a new
# kind doesn't have to touch this file before it can write anything, but
# every kind that matters gets its real convention named explicitly.
_KIND_LAYOUT: dict[str, str] = {
    "parser": "Parsers/{name}.yaml",
    "analytic_rule": "Analytic Rules/{name}.yaml",
    "workbook": "Workbooks/{name}.json",
    "tdd": "{name}.md",
}


def _sanitize(segment: str) -> str:
    """Collapse a human-supplied name into one safe path segment.

    No slashes survive, so a vendor/product/name containing `/`, `../`, or an
    absolute path can never escape the solution's own folder. An
    all-punctuation input (e.g. "..") strips down to empty and falls back to
    a fixed placeholder rather than resolving to "the parent directory".
    """
    cleaned = _UNSAFE.sub("_", segment).strip(" ._")
    return cleaned or "unnamed"


def write_artifact(output_dir: Path, *, solution: str, kind: str, name: str, content: str) -> str:
    """Write one artifact file under ``{output_dir}/{solution}/...``.

    Returns the path as it appears on the *host* side of the docker-compose
    mount (``output/...``) — that's what's actually useful to hand back to
    the analyst, not the in-container ``/output/...`` path.
    """
    template = _KIND_LAYOUT.get(kind, "{kind}/{name}.txt")
    rel = template.format(name=_sanitize(name), kind=_sanitize(kind))
    solution_dir = _sanitize(solution)

    root = output_dir.resolve()
    target = (output_dir / solution_dir / rel).resolve()
    if root != target and root not in target.parents:
        # Sanitization above should make this unreachable; kept as a hard
        # backstop since this function writes to disk from model-influenced
        # input.
        raise ValueError(f"refusing to write outside the output directory: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    return str(Path("output") / solution_dir / rel)
