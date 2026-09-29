"""Shared low-level `claude` CLI subprocess invocation.

Used by both ClaudeCliRuntime (generation) and openai_compat (the n8n
orchestrator's own chat-model, proxied through this sidecar so it can also
run on a Claude Code subscription instead of an Anthropic API key). Nothing
here knows about the generation Tool dataclass or OpenAI request shapes —
callers build their own --json-schema and system prompt text and get back a
plain (envelope, tool_trace) pair. See app/llm/claude_cli.py's module
docstring for the underlying design rationale (why --json-schema, why no
run_kql/run_python, why the subprocess env is built explicitly).
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .base import LLMUnavailable

_RATE_LIMIT_MARKERS = ("rate limit", "rate_limit", "usage limit", "429")


@functools.lru_cache(maxsize=8)
def resolve_bin(bin_path: str) -> str:
    """Resolve the `claude` binary path. Cached per bin_path (a PATH scan is
    cheap but there's no reason to repeat it on every call in a process)."""
    candidate = bin_path or shutil.which("claude")
    if not candidate or not Path(candidate).exists():
        raise LLMUnavailable(
            "no `claude` binary was found. Set CLAUDE_CLI_BIN to its path, "
            "or install it (npm install -g @anthropic-ai/claude-code)."
        )
    return candidate


def subprocess_env(oauth_token: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
    }
    if oauth_token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
    # No credential persistence needed once the token is in the env; a
    # scratch dir avoids the CLI writing session state under $HOME.
    env.setdefault("CLAUDE_CONFIG_DIR", str(Path(tempfile.gettempdir()) / "claude-cli-config"))
    return env


def build_command(
    *,
    bin_path: str,
    system: str,
    model: str,
    schema: dict[str, Any],
    add_dir: str | None = None,
) -> list[str]:
    cmd = [
        bin_path,
        "--print",
        "--output-format", "stream-json",
        "--verbose",
        "--system-prompt", system,
        # Read/Grep/Glob are read-only; offering them costs nothing even
        # when the caller has no --add-dir for them to usefully target.
        "--tools", "Read,Grep,Glob",
        "--permission-mode", "bypassPermissions",
        "--strict-mcp-config",
        # Empty: don't auto-load user/project/local settings, hooks, or a
        # stray CLAUDE.md — behavior must not depend on whatever happens to
        # be on this host.
        "--setting-sources", "",
        "--model", model,
        "--json-schema", json.dumps(schema),
    ]
    if add_dir:
        cmd += ["--add-dir", add_dir]
    return cmd


def parse_stream(stdout: str) -> tuple[dict[str, Any], list[str], str]:
    """Parse --output-format stream-json NDJSON output into the final result
    envelope, an ordered tool-name trace, and any extended-thinking text.

    Thinking can arrive two ways depending on CLI version/flags: as a whole
    "thinking" content block on an "assistant" message, or streamed
    incrementally as "stream_event" -> content_block_delta ->
    "thinking_delta" chunks. Both are collected so callers get the full text
    either way."""
    envelope: dict[str, Any] = {}
    tool_trace: list[str] = []
    thinking_parts: list[str] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            outer = json.loads(line)
        except json.JSONDecodeError:
            continue
        outer_type = outer.get("type")
        if outer_type == "assistant":
            for block in (outer.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_use":
                    name = block.get("name", "")
                    # StructuredOutput is the synthetic --json-schema
                    # submission tool, not a real tool call worth tracing.
                    if name and name != "StructuredOutput":
                        tool_trace.append(name)
                elif btype == "thinking":
                    chunk = block.get("thinking", "")
                    if chunk:
                        thinking_parts.append(chunk)
        elif outer_type == "stream_event":
            delta = ((outer.get("event") or {}).get("delta")) or {}
            if delta.get("type") == "thinking_delta":
                chunk = delta.get("thinking", "")
                if chunk:
                    thinking_parts.append(chunk)
        elif outer_type == "result":
            envelope = outer
    return envelope, tool_trace, "".join(thinking_parts)


async def run_claude_once(
    *,
    bin_path: str,
    oauth_token: str,
    timeout: int,
    system: str,
    user_message: str,
    model: str,
    schema: dict[str, Any],
    add_dir: str | None = None,
) -> tuple[dict[str, Any], list[str], str]:
    """One full `claude --print` invocation: resolve, build, execute, parse,
    and raise LLMUnavailable on any backend-level failure (auth, timeout,
    rate limit, no output). Returns (envelope, tool_trace, thinking) on
    success — callers interpret `envelope["structured_output"]`/`["result"]`
    themselves, since what a "successful" response means differs between
    the generation flow and the chat-model proxy. `thinking` is the
    concatenated extended-thinking text, or "" when the model produced none
    or the CLI didn't stream it — purely for debug logging, no caller
    branches on it."""
    resolved = resolve_bin(bin_path)
    cmd = build_command(bin_path=resolved, system=system, model=model, schema=schema, add_dir=add_dir)

    scratch = Path(tempfile.mkdtemp(prefix="claude-cli-run-"))
    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(scratch),
                env=subprocess_env(oauth_token),
            )
        except FileNotFoundError as exc:
            raise LLMUnavailable(f"could not start `claude` CLI: {exc}") from exc

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=user_message.encode("utf-8")),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise LLMUnavailable(f"claude CLI timed out after {timeout}s") from exc
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    stdout = stdout_b.decode("utf-8", errors="replace").strip()
    stderr = stderr_b.decode("utf-8", errors="replace").strip()

    if not stdout:
        raise LLMUnavailable(
            f"claude CLI produced no output (exit {proc.returncode}): {stderr[:400]}"
        )

    envelope, tool_trace, thinking = parse_stream(stdout)
    if not envelope:
        raise LLMUnavailable(
            f"claude CLI stream produced no result envelope "
            f"(exit {proc.returncode}): {stderr[:400] or stdout[:400]}"
        )

    if envelope.get("is_error"):
        message = str(envelope.get("result") or stderr or "unknown error")
        lowered = message.lower()
        if any(marker in lowered for marker in _RATE_LIMIT_MARKERS):
            raise LLMUnavailable(f"claude CLI rate limited: {message[:400]}")
        if "not authenticated" in lowered or "not logged in" in lowered:
            raise LLMUnavailable(
                "claude CLI is not authenticated. Run `claude setup-token` "
                "on a logged-in machine and set CLAUDE_CODE_OAUTH_TOKEN."
            )
        raise LLMUnavailable(f"claude CLI error: {message[:400]}")

    if envelope.get("subtype") == "error_max_turns":
        raise LLMUnavailable("claude CLI hit its internal turn limit without finishing")

    return envelope, tool_trace, thinking
