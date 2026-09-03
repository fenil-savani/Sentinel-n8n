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


def _resolve_target(output_dir: Path, solution: str, rel: str) -> tuple[Path, str]:
    solution_dir = _sanitize(solution)

    root = output_dir.resolve()
    target = (output_dir / solution_dir / rel).resolve()
    if root != target and root not in target.parents:
        # Sanitization above should make this unreachable; kept as a hard
        # backstop since this function writes to disk from model-influenced
        # input.
        raise ValueError(f"refusing to write outside the output directory: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    # output/ is a bind mount the analyst manages with their own host tools,
    # not container-internal state, and this process's uid (10001 in the
    # container, mapped to some host uid the analyst doesn't share) has no
    # group in common with the analyst's host user either — so the analyst
    # needs the *other* bits, not just group ones. mkdir()'s default mode is
    # reduced by umask, so set it explicitly on every directory this call
    # creates, from the leaf up to (but not including) the mount root, which
    # is owned by the host user and not ours to chmod.
    for parent in (target.parent, *target.parent.parents):
        if parent == root:
            break
        parent.chmod(0o777)
    return target, str(Path("output") / solution_dir / rel)


def write_artifact(output_dir: Path, *, solution: str, kind: str, name: str, content: str) -> str:
    """Write one artifact file under ``{output_dir}/{solution}/...``.

    Returns the path as it appears on the *host* side of the docker-compose
    mount (``output/...``) — that's what's actually useful to hand back to
    the analyst, not the in-container ``/output/...`` path.
    """
    template = _KIND_LAYOUT.get(kind, "{kind}/{name}.txt")
    rel = template.format(name=_sanitize(name), kind=_sanitize(kind))
    target, host_path = _resolve_target(output_dir, solution, rel)
    target.write_text(content, encoding="utf-8")
    target.chmod(0o666)
    return host_path


def write_ccf_connector_files(
    output_dir: Path, *, solution: str, name: str, files: dict[str, str]
) -> dict[str, str]:
    """Write a CCF connector's file set under ``{Solution}/Data Connectors/{name}_ccp/``,
    mirroring the layout real Azure-Sentinel solutions use (see the reference examples
    under skills/generate-sentinel-ccf-connector/reference/).

    Unlike every other artifact here, one CCF draft is several files, not one — so this
    is a sibling to `write_artifact` rather than a new `_KIND_LAYOUT` entry (whose
    template only has room for a single `{name}` output path).

    ``files`` keys are the fixed CCF suffixes (``ConnectorDefinition``, ``PollerConfig``,
    ``DCR``, and optionally ``Table``); returns the same keys mapped to their host-side
    display paths.
    """
    safe_name = _sanitize(name)
    folder = f"Data Connectors/{safe_name}_ccp"
    host_paths: dict[str, str] = {}
    for suffix, content in files.items():
        rel = f"{folder}/{safe_name}_{suffix}.json"
        target, host_path = _resolve_target(output_dir, solution, rel)
        target.write_text(content, encoding="utf-8")
        target.chmod(0o666)
        host_paths[suffix] = host_path
    return host_paths


def write_binary_artifact(
    output_dir: Path, *, solution: str, rel_name: str, content: bytes
) -> tuple[Path, str]:
    """Write raw bytes under ``{output_dir}/{solution}/{rel_name}``.

    For files that don't fit the text-based ``_KIND_LAYOUT`` convention above
    (a rendered ``.docx``, a ``.drawio`` diagram) but still need to land next
    to their sibling artifact in the same sandboxed solution folder.
    ``rel_name`` is a filename already-sanitized by the caller (e.g. built
    from a draft id), not a free-form kind/name pair.

    Returns ``(disk_path, host_path)`` — the real path for a caller that
    needs to read the file back (e.g. to render it), and the host-side
    display path (``output/...``), same convention as `write_artifact`.
    """
    target, host_path = _resolve_target(output_dir, solution, _sanitize(rel_name))
    target.write_bytes(content)
    target.chmod(0o666)
    return target, host_path
