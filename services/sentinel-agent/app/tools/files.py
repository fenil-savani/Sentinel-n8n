"""Reference-file access for the agent, confined to REFERENCE_DIR.

Both skill files instruct the model to read an existing artifact before writing
a new one ("If one exists, READ IT FIRST and mirror its structure"). This is
what makes that possible without handing the model the filesystem.

Paths are model-supplied and therefore untrusted: every path is resolved to its
canonical form and rejected unless it stays inside the reference root. That
covers `..`, absolute paths, and symlinks out of the tree.
"""

from __future__ import annotations

from pathlib import Path

#: Reading a whole 178 KB dashboard into context would blow the window and is
#: never the right move — templates are extracted from it at build time instead.
MAX_READ_BYTES = 120_000


class ReferenceFiles:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _resolve(self, rel: str) -> Path:
        candidate = (self._root / rel.lstrip("/")).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise ValueError(f"path escapes the reference directory: {rel}")
        return candidate

    def list_files(self) -> str:
        if not self._root.exists():
            return "(reference directory is empty)"
        entries = sorted(p for p in self._root.rglob("*") if p.is_file())
        if not entries:
            return "(reference directory is empty)"
        return "\n".join(
            f"{p.relative_to(self._root)}  ({p.stat().st_size:,} bytes)" for p in entries
        )

    def read(self, path: str, max_bytes: int = MAX_READ_BYTES) -> str:
        target = self._resolve(path)
        if not target.is_file():
            return (
                f"ERROR: no such reference file '{path}'.\n"
                f"Available files:\n{self.list_files()}"
            )

        size = target.stat().st_size
        text = target.read_text(encoding="utf-8", errors="replace")
        if size > max_bytes:
            return (
                f"WARNING: '{path}' is {size:,} bytes; showing the first "
                f"{max_bytes:,}. Use run_python to inspect it programmatically "
                f"instead of reading it whole.\n\n" + text[:max_bytes]
            )
        return text
