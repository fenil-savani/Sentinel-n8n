"""Claude CLI runtime — subprocess wrapper around the local `claude` binary.

Alternative to AnthropicRuntime for a Claude Code subscription (browser/OAuth
login) instead of billing an Anthropic Console API key directly. Selected via
LLM_PROVIDER=claude_cli.

Trust boundary and design notes worth knowing before editing this file:

* The `claude` CLI owns its entire internal tool-calling loop within one
  subprocess call — there is no way to inject an arbitrary Python function
  (run_kql, run_python) into that loop without a real MCP server, which does
  not exist in this codebase. So this runtime does NOT execute run_kql /
  run_python / read_reference / list_reference_files: file reads are covered
  by the CLI's own native Read/Grep/Glob tools (scoped to the reference dir
  via --add-dir), and the two live-Azure self-check tools are simply
  unavailable during generation. This is a real but acceptable trade-off:
  the actual safety gate — a fresh lint + KQL query — runs unconditionally
  in Python *after* the agent returns, in generators/parser.py and
  generators/workbook.py, regardless of which runtime produced the draft.
  Dropping the model's own mid-generation self-check doesn't weaken that
  gate, it only removes a convenience. If generation quality measurably
  regresses without it, the fix is an in-process MCP tool (claude-agent-sdk),
  not a Bash tool — Bash would hand model-generated shell a path to this
  container's Azure secrets (AZURE_CLIENT_SECRET / AZURE_LOGS_CLIENT_SECRET
  are in its environment) and throws away the sandboxing tools/python_exec.py
  already provides.

* Structured output uses --json-schema rather than asking the model to emit
  a fenced JSON block. The CLI validates against the schema internally (via
  a synthetic `StructuredOutput` tool) and echoes the already-parsed result
  on the final envelope as `structured_output` — no text parsing needed on
  the happy path. The schema is built mechanically from every terminal=True
  tool passed into run(), so Toolbox stays the single source of schema
  truth — but it is a single flat object with a "tool" enum discriminator,
  NOT a `oneOf` of per-tool variants: verified live against the API that
  Anthropic's tool input_schema rejects oneOf/allOf/anyOf at the top level
  ("input_schema does not support oneOf, allOf, or anyOf at the top level").
  Because the schema can only require "tool" itself (not conditionally
  require a chosen variant's fields), run() re-validates that the fields the
  *chosen* tool actually requires are present in the payload before trusting
  it — see the comment in run() below.

* `max_tokens` (accepted by build_runtime) and `max_iterations` (accepted by
  AgentRuntime.run) have no equivalent in this CLI version (no --max-turns)
  and are accepted-but-ignored here.

* The actual subprocess invocation (binary resolution, env, command
  construction, NDJSON parsing, error mapping) lives in app/llm/cli_exec.py,
  shared with app/llm/openai_compat.py — the n8n orchestrator's own chat
  model is proxied through the same CLI mechanism via a /v1/chat/completions
  endpoint, since n8n has no "point at a local CLI" option of its own but
  its OpenAI Chat Model node accepts a custom base URL. This module only
  builds the generation-specific schema and maps the result to RunResult.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from . import cli_exec
from .base import AgentRuntime, RunResult, Tool

log = logging.getLogger(__name__)


class ClaudeCliRuntime(AgentRuntime):
    def __init__(
        self,
        *,
        reference_dir: Path,
        bin_path: str = "",
        oauth_token: str = "",
        timeout: int = 600,
    ) -> None:
        # Deliberately lazy: resolving the binary or validating auth here
        # would crash-loop the sidecar the moment LLM_PROVIDER=claude_cli is
        # set, even though every other endpoint needs neither — same
        # "degrade gracefully, error only when the feature is used" pattern
        # as AnthropicRuntime.__init__. cli_exec.resolve_bin() isn't called
        # until run().
        self._reference_dir = reference_dir
        self._bin_path = bin_path
        self._oauth_token = oauth_token
        self._timeout = timeout

    async def aclose(self) -> None:
        return None

    def supported_tool_names(self, tools: list[Tool]) -> list[str]:
        # Only terminal tools become structured-output schema variants (see
        # _output_schema); non-terminal Python tools are never invoked by
        # this runtime, so the harness must not claim they exist.
        return [t.name for t in tools if t.terminal]

    # ── schema construction ──────────────────────────────────────────────

    @staticmethod
    def _output_schema(terminal_tools: list[Tool]) -> dict[str, Any]:
        """One flat object schema covering every terminal tool's fields,
        keyed by a "tool" enum discriminator — not a `oneOf`, which
        Anthropic's tool input_schema rejects at the top level. Only "tool"
        is required at the schema level; run() checks the chosen tool's own
        required fields are present before trusting the payload."""
        if not terminal_tools:
            raise ValueError("ClaudeCliRuntime requires at least one terminal tool")

        properties: dict[str, Any] = {
            "tool": {"type": "string", "enum": [t.name for t in terminal_tools]}
        }
        for t in terminal_tools:
            for prop_name, prop_schema in (t.parameters.get("properties") or {}).items():
                properties.setdefault(prop_name, prop_schema)

        return {
            "type": "object",
            "properties": properties,
            "required": ["tool"],
            "additionalProperties": False,
        }

    # ── execution ────────────────────────────────────────────────────────

    async def run(
        self,
        *,
        system: str,
        user: str,
        tools: list[Tool],
        model: str,
        max_iterations: int = 24,  # no CLI equivalent (no --max-turns); ignored
    ) -> RunResult:
        by_name = self._index(tools)
        schema = self._output_schema([t for t in tools if t.terminal])

        envelope, tool_trace = await cli_exec.run_claude_once(
            bin_path=self._bin_path,
            oauth_token=self._oauth_token,
            timeout=self._timeout,
            system=system,
            user_message=user,
            model=model,
            schema=schema,
            add_dir=str(self._reference_dir),
        )

        text = envelope.get("result") or ""
        payload = envelope.get("structured_output")
        if payload is None and text:
            # Should not happen when --json-schema is honoured, but if the
            # model exhausted its turns or stopped before ever calling the
            # synthetic StructuredOutput tool, fall back to raw text rather
            # than raising — a bad generation, not an outage. parser.py's
            # no_submission path and workbook.py's retry loop already handle
            # a None terminal_tool correctly.
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None

        tool_name = payload.get("tool") if isinstance(payload, dict) else None
        chosen = by_name.get(tool_name) if tool_name else None
        # The schema can only require the "tool" field itself (Anthropic
        # rejects a conditional oneOf), so a well-formed "tool" value doesn't
        # guarantee that tool's own required fields were actually filled in —
        # check explicitly rather than trusting the schema alone.
        if chosen is not None and all(
            field in payload for field in chosen.parameters.get("required", [])
        ):
            terminal_tool: str | None = tool_name
            payload = {k: v for k, v in payload.items() if k != "tool"}
        else:
            terminal_tool = None
            payload = None

        log.info(
            "claude cli OK | session=%s turns=%s cost=$%.4f",
            envelope.get("session_id"), envelope.get("num_turns", 0),
            envelope.get("total_cost_usd", 0.0),
        )

        return RunResult(
            terminal_tool=terminal_tool,
            payload=payload,
            text=text,
            iterations=envelope.get("num_turns", 0),
            tool_trace=tool_trace,
        )
