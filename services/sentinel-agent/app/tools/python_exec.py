"""Sandboxed Python execution.

Both skills explicitly ask for this. The parser skill: "create python script to
derive if possible to reduce token" — deriving a schema by unioning keys across
thousands of sample records is code work, not token work. The workbook skill's
Step 10 is a literal `json.load` validity check.

Trust boundary. The code is model-generated and therefore untrusted. Defences,
in order of how much they actually buy:

1. The container is the boundary. It runs as an unprivileged user, its only
   mounts are read-only, and it holds no Azure write credentials (the deploy
   service principal lives in n8n, not here).
2. Per-call temp cwd, deleted afterwards.
3. Wall-clock timeout, plus RLIMIT_AS and RLIMIT_CPU so a runaway allocation or
   busy loop cannot take the sidecar down with it.

This is *not* a security sandbox against a determined adversary — it is
containment for a model that writes a bad loop. Do not widen the mounts or add
write credentials to this container without revisiting that.
"""

from __future__ import annotations

import resource
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MEMORY_LIMIT_BYTES = 512 * 1024 * 1024
OUTPUT_LIMIT_CHARS = 20_000


def _apply_limits(cpu_seconds: int) -> None:
    """Runs in the child between fork and exec."""
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))


def run_python(code: str, *, timeout: int = 30, reference_dir: Path | None = None) -> str:
    """Execute `code` and return combined stdout/stderr.

    The reference directory is exposed read-only as REFERENCE_DIR so scripts can
    load sample data without the model needing to inline it into the prompt.
    """
    workdir = Path(tempfile.mkdtemp(prefix="agent-py-"))
    try:
        script = workdir / "script.py"
        preamble = ""
        if reference_dir is not None:
            preamble = (
                "import os\n"
                f"REFERENCE_DIR = {str(reference_dir)!r}\n"
                "os.environ['REFERENCE_DIR'] = REFERENCE_DIR\n"
            )
        script.write_text(preamble + code, encoding="utf-8")

        try:
            proc = subprocess.run(  # noqa: S603 - deliberate, see module docstring
                [sys.executable, "-I", str(script)],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
                preexec_fn=lambda: _apply_limits(timeout),
            )
        except subprocess.TimeoutExpired:
            return (
                f"ERROR: execution exceeded {timeout}s and was killed. "
                "Reduce the work, or process the data in chunks."
            )
        except MemoryError:
            return f"ERROR: exceeded the {MEMORY_LIMIT_BYTES // 1024 // 1024} MB memory limit."

        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()

        parts: list[str] = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"--- stderr ---\n{err}")
        if proc.returncode != 0:
            parts.append(f"--- exit code {proc.returncode} ---")
        if not parts:
            return "(no output — remember to print() the result you want to see)"

        combined = "\n".join(parts)
        if len(combined) > OUTPUT_LIMIT_CHARS:
            return (
                combined[:OUTPUT_LIMIT_CHARS]
                + f"\n\n... truncated at {OUTPUT_LIMIT_CHARS:,} chars. "
                "Print a summary rather than the whole structure."
            )
        return combined
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
