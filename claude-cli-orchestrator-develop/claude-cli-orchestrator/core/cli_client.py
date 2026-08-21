"""
cli_client.py -- Low-level wrapper around the Claude CLI binary.

Responsibility:
  - Build the subprocess command
  - Execute Claude CLI in non-interactive (--print) mode
  - Capture stdout / stderr
  - Parse the JSON envelope returned by --output-format json
  - Expose the raw text result to callers

Nothing about agent logic, tool routing, or JSON extraction lives here.
This module is the single place that knows how to INVOKE the binary.
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from config.settings import (
    CLAUDE_BIN,
    DEFAULT_MODEL,
    VERBOSE,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

class ClaudeResponse:
    """Structured result of a single Claude CLI invocation."""

    def __init__(
        self,
        raw_text: str,
        session_id: str,
        cost_usd: float,
        num_turns: int,
        is_error: bool,
        raw_stdout: str,
        raw_stderr: str,
        input_tokens:               int = 0,
        output_tokens:              int = 0,
        cache_read_input_tokens:    int = 0,
        cache_creation_input_tokens: int = 0,
        duration_ms:                int = 0,
        thinking:                   str = "",
        tool_calls:                 list | None = None,
    ):
        self.raw_text   = raw_text       # The agent's text response (result field)
        self.session_id = session_id
        self.cost_usd   = cost_usd
        self.num_turns  = num_turns
        self.is_error   = is_error
        self.raw_stdout = raw_stdout
        self.raw_stderr = raw_stderr
        # Token usage (from envelope.usage)
        self.input_tokens                = input_tokens
        self.output_tokens               = output_tokens
        self.cache_read_input_tokens     = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.duration_ms                 = duration_ms
        # Extended thinking content collected from stream-json events
        self.thinking                    = thinking
        # Ordered list of tool calls: [{"name": "Read", "input": {"file_path": "..."}}, ...]
        self.tool_calls: list[dict]      = tool_calls if tool_calls is not None else []

    def __repr__(self) -> str:
        preview = self.raw_text[:120].replace("\n", " ")
        return (
            f"<ClaudeResponse session={self.session_id} "
            f"turns={self.num_turns} cost=${self.cost_usd:.4f} "
            f"in={self.input_tokens} out={self.output_tokens} "
            f"thinking={len(self.thinking)}chars "
            f"error={self.is_error} preview='{preview}...'>"
        )


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------

class ClaudeCliClient:
    """
    Thin subprocess wrapper for Claude CLI.

    Usage
    -----
    client = ClaudeCliClient()
    response = client.run(
        system_prompt="You are ...",
        user_message="Do X with this data: ...",
        allowed_tools=["Read", "Grep", "mcp__context-engine__context_engine_agent"],
        mcp_config_path="/abs/path/to/mcp.json",
        working_dir="/abs/path/to/project_root",  # agent's cwd for Read/Grep
    )
    print(response.raw_text)
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        timeout: int = 300,
        verbose: bool = VERBOSE,
    ):
        self.model   = model
        self.timeout = timeout
        self.verbose = verbose

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        system_prompt: str,
        user_message: str,
        allowed_tools: list[str] | None = None,
        mcp_config_path: str | Path | None = None,
        working_dir: str | Path | None = None,
        extra_flags: list[str] | None = None,
    ) -> ClaudeResponse:
        """
        Execute one non-interactive Claude CLI call and return the response.

        Parameters
        ----------
        system_prompt     : Full system instructions (agent MD content).
        user_message      : The user-turn content (JSON payload string or plain text).
        allowed_tools     : Which tools the agent may use, e.g. ["Read","Grep","mcp__*"].
                            Pass None to use CLI defaults.
        mcp_config_path   : Absolute path to an mcp.json file.
        working_dir       : Directory the subprocess cwd is set to.
                            Agents use this as the base for Read/Grep relative paths.
        extra_flags       : Any additional raw CLI flags to append.

        Returns
        -------
        ClaudeResponse with .raw_text containing the agent's output.

        Raises
        ------
        RuntimeError   if the CLI exits with a non-zero code and is_error is True.
        """
        cmd, stdin_input = self._build_command(
            system_prompt=system_prompt,
            user_message=user_message,
            allowed_tools=allowed_tools,
            mcp_config_path=mcp_config_path,
            extra_flags=extra_flags,
        )

        if self.verbose:
            parts = [
                (item[:500] + "...") if len(item) > 500 else item
                for item in cmd
            ]
            logger.debug("Claude CLI command:\n  %s", "\n  ".join(parts))

        return self._execute(cmd, stdin_input=stdin_input, working_dir=working_dir)

    # ------------------------------------------------------------------
    # Command construction
    # ------------------------------------------------------------------

    def _build_command(
        self,
        system_prompt: str,
        user_message: str,
        allowed_tools: list[str] | None,
        mcp_config_path: str | Path | None,
        extra_flags: list[str] | None,
    ) -> tuple[list[str], str | None]:
        """
        Build the Claude CLI command and optionally return the user message for stdin.

        Returns a tuple of (command_list, stdin_input).
        If stdin_input is not None, the user_message should be passed via stdin.
        """
        cmd: list[str] = [
            CLAUDE_BIN,
            "--print",                        # non-interactive, exit after response
            "--output-format", "stream-json", # NDJSON stream: thinking blocks + final result
            "--verbose",                      # required by CLI when using stream-json with --print
            "--dangerously-skip-permissions",  # no approval prompts in automation
            "--model", self.model,
        ]

        # System prompt
        if system_prompt:
            cmd += ["--system-prompt", system_prompt]

        # Tool allowlist
        # --allowedTools accepts comma-separated values or space-separated values.
        # We join with commas to handle tool names that contain spaces.
        if allowed_tools:
            cmd += ["--allowedTools", ",".join(allowed_tools)]

        # MCP config
        if mcp_config_path:
            path = Path(mcp_config_path)
            if path.exists():
                cmd += ["--mcp-config", str(path.resolve())]
            else:
                logger.warning("mcp_config_path does not exist, skipping: %s", mcp_config_path)

        # Extra caller-supplied flags (e.g. --add-dir, --verbose)
        if extra_flags:
            cmd += extra_flags

        # Return command and user_message for stdin (to avoid command-line length limits)
        return cmd, user_message

    # ------------------------------------------------------------------
    # Execution and response parsing
    # ------------------------------------------------------------------

    def _execute(
        self,
        cmd: list[str],
        stdin_input: str | None = None,
        working_dir: str | Path | None = None,
    ) -> ClaudeResponse:
        cwd = str(working_dir) if working_dir else None

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",   # Force UTF-8 to handle Unicode in tool call output (Windows cp1252 fails)
                errors="replace",   # Replace un-decodable bytes rather than crashing
                input=stdin_input,  # Pass user_message via stdin to avoid command-line length limits
                timeout=self.timeout,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Claude CLI timed out after {self.timeout}s. "
                "Increase AGENT_TIMEOUT in config/settings.py or simplify the prompt."
            )

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        if self.verbose and stderr:
            logger.debug("Claude CLI stderr:\n%s", stderr)

        if not stdout:
            raise RuntimeError(
                f"Claude CLI produced no output.\n"
                f"Return code: {proc.returncode}\n"
                f"stderr: {stderr[:500]}"
            )

        envelope, thinking, tool_calls = self._parse_stream(stdout)

        if not envelope:
            raise RuntimeError(
                f"Claude CLI stream-json produced no result envelope.\n"
                f"Return code: {proc.returncode}\n"
                f"stdout (first 500 chars): {stdout[:500]}\n"
                f"stderr: {stderr[:500]}"
            )

        is_error  = envelope.get("is_error", False) or proc.returncode != 0
        raw_text  = envelope.get("result", "")
        session   = envelope.get("session_id", "")
        cost      = envelope.get("total_cost_usd", envelope.get("cost_usd", 0.0))
        turns     = envelope.get("num_turns", 0)
        dur_ms    = envelope.get("duration_ms", 0)

        # Token counts from envelope.usage (available in Claude CLI >= 2.x)
        usage = envelope.get("usage") or {}
        input_tokens                = usage.get("input_tokens", 0)
        output_tokens               = usage.get("output_tokens", 0)
        cache_read_input_tokens     = usage.get("cache_read_input_tokens", 0)
        cache_creation_input_tokens = usage.get("cache_creation_input_tokens", 0)

        if is_error:
            logger.error(
                "Claude CLI returned is_error=True.\nsession=%s\nresult=%s",
                session,
                raw_text[:400],
            )
            raise RuntimeError(
                f"Claude CLI error (session={session}):\n{raw_text[:800]}"
            )

        if thinking:
            logger.debug(
                "Claude CLI thinking captured | session=%s chars=%d",
                session, len(thinking),
            )

        logger.info(
            "Claude CLI OK | session=%s turns=%d cost=$%.4f in=%d out=%d cache_read=%d thinking=%dchars",
            session, turns, cost, input_tokens, output_tokens, cache_read_input_tokens, len(thinking),
        )

        return ClaudeResponse(
            raw_text=raw_text,
            session_id=session,
            cost_usd=cost,
            num_turns=turns,
            is_error=is_error,
            raw_stdout=stdout,
            raw_stderr=stderr,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            duration_ms=dur_ms,
            thinking=thinking,
            tool_calls=tool_calls,
        )

    # ------------------------------------------------------------------
    # NDJSON stream parser
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_stream(stdout: str) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
        """
        Parse Claude CLI --output-format stream-json NDJSON output.

        Claude CLI emits one JSON object per line in a conversation-turn format:

          {"type": "system",    ...}                -- init (once)
          {"type": "assistant", "message": {...}}   -- full assistant turn; content[]
                                                       may include tool_use blocks
          {"type": "user",      "message": {...}}   -- tool results returned to model
          {"type": "stream_event", "event": {...}}  -- real-time deltas (thinking_delta)
          {"type": "result",    ...}                -- final result envelope (cost, turns, ...)

        Tool calls are captured from assistant.message.content[] blocks of type "tool_use".
        Thinking text is captured from stream_event deltas of type "thinking_delta".
        (Both may coexist when extended thinking is enabled.)

        Returns (result_envelope, thinking_text, tool_calls).
          result_envelope : {} if no result line found (caller raises).
          thinking_text   : "" if no thinking blocks were streamed.
          tool_calls      : ordered list of {"name": str, "input": dict} for every
                            tool call across all assistant turns.
        """
        thinking_parts:  list[str]           = []
        result_envelope: dict[str, Any]      = {}
        tool_calls:      list[dict[str, Any]] = []

        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                outer = json.loads(line)
            except json.JSONDecodeError:
                continue

            outer_type = outer.get("type")

            # --- Full assistant turn: extract tool_use and thinking content blocks ---
            if outer_type == "assistant":
                msg     = outer.get("message", {})
                content = msg.get("content", [])
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "tool_use":
                        tool_calls.append({
                            "name":  block.get("name", ""),
                            "input": block.get("input", {}),
                        })
                    elif btype == "thinking":
                        # Thinking may appear as a content block in the assistant message
                        chunk = block.get("thinking", "")
                        if chunk:
                            thinking_parts.append(chunk)

            # --- Real-time streaming delta (thinking_delta from extended thinking) ---
            elif outer_type == "stream_event":
                inner = outer.get("event", {})
                if (
                    inner.get("type") == "content_block_delta"
                    and inner.get("delta", {}).get("type") == "thinking_delta"
                ):
                    chunk = inner["delta"].get("thinking", "")
                    if chunk:
                        thinking_parts.append(chunk)

            # --- Final result envelope ---
            elif outer_type == "result":
                result_envelope = outer

        return result_envelope, "".join(thinking_parts), tool_calls
