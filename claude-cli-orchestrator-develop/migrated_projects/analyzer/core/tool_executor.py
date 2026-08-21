"""
tool_executor.py -- Tool calling model for the Claude CLI project.

-----------------------------------------------------------------------
IMPORTANT: How tool calling works with Claude CLI
-----------------------------------------------------------------------

Claude CLI handles the FULL tool-call loop natively when you run with
--print mode.  When the LLM decides to call a tool:

  1. CLI detects the tool_use block in the model response
  2. CLI executes the tool (built-in or MCP)
  3. CLI appends the tool result to the conversation
  4. CLI sends the updated conversation back to the model
  5. Repeat until model stops calling tools -> returns final text

This means Read, Grep, Glob, Write, and all MCP tools (context-engine,
google-secops-mcp-server, etc.) execute AUTOMATICALLY -- no Python code
needed to implement them.

Role of this module
-----------------------------------------------------------------------
This module handles the ONLY two cases that fall outside the native CLI
tool loop:

  A. JSON extraction from the final agent text output
     Agents return structured JSON inside their text response.
     We need to reliably extract and validate that JSON.

  B. Custom Python tools (optional extension point)
     If you add Python-side tools in the future (e.g., database lookup,
     custom validators), register them here as `CustomTool` instances
     and the AgentRunner will detect and route tool calls to them.

For the current query-generator workflow, only case A is needed.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# A: JSON extraction from agent text responses
# ---------------------------------------------------------------------------

class JsonExtractionError(Exception):
    """Raised when no valid JSON can be found in an agent response."""


def extract_json_from_text(text: str) -> dict[str, Any]:
    """
    Extract the last valid JSON object from a raw agent text response.

    Agents are instructed to return JSON only, but in practice they may
    wrap it in markdown fences, prefix it with reasoning text, or add
    trailing newlines.  This function is resilient to all of those.

    Extraction order (first match wins):
      1. Last ```json ... ``` fenced block
      2. Last ``` ... ``` fenced block (unlabelled)
      3. Last top-level {...} in the raw text
      4. The entire text if it parses as JSON

    Raises JsonExtractionError if nothing parses.
    """
    if not text or not text.strip():
        raise JsonExtractionError("Agent returned empty text -- no JSON to extract.")

    # Strategy 1 & 2: fenced code blocks
    fenced_pattern = re.compile(r"```(?:json)?\s*([\s\S]+?)\s*```", re.MULTILINE)
    matches = fenced_pattern.findall(text)
    if matches:
        # Try from last to first (prefer the final JSON block)
        for candidate in reversed(matches):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    # Strategy 3: outermost { ... } spanning the widest range
    brace_start = text.find("{")
    brace_end   = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        candidate = text[brace_start : brace_end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Strategy 4: parse entire text as-is
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    raise JsonExtractionError(
        f"No valid JSON found in agent response. "
        f"First 500 chars:\n{text[:500]}"
    )


def safe_extract_json(text: str, fallback: dict | None = None) -> dict[str, Any]:
    """
    Non-raising variant of extract_json_from_text.

    Returns `fallback` (default: empty dict) and logs a warning if extraction
    fails.  Use this when a missing JSON output should not crash the pipeline.
    """
    try:
        return extract_json_from_text(text)
    except JsonExtractionError as exc:
        logger.warning("JSON extraction failed: %s", exc)
        return fallback if fallback is not None else {}


# ---------------------------------------------------------------------------
# B: Custom Python tool registry (extension point)
# ---------------------------------------------------------------------------

@dataclass
class CustomTool:
    """
    Descriptor for a Python-side tool that the orchestrator can call
    when an agent output signals a tool invocation.

    NOTE: For the current query-generator project, all tools (Read, Grep,
    MCP tools) are handled natively by Claude CLI.  This class is here as
    an extension point for future custom Python tools.

    Usage example:
        def my_python_tool(args: dict) -> dict:
            return {"result": "..."}

        registry.register(CustomTool(
            name="my_python_tool",
            description="Does something useful",
            handler=my_python_tool,
        ))
    """
    name:        str
    description: str
    handler:     Callable[[dict[str, Any]], Any]
    schema:      dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    """Registry of custom Python-side tools."""

    def __init__(self):
        self._tools: dict[str, CustomTool] = {}

    def register(self, tool: CustomTool) -> None:
        """Add a tool to the registry."""
        self._tools[tool.name] = tool
        logger.debug("Registered custom tool: %s", tool.name)

    def get(self, name: str) -> CustomTool | None:
        """Look up a tool by name."""
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        """Return names of all registered tools."""
        return list(self._tools.keys())

    def execute(self, tool_name: str, args: dict[str, Any]) -> Any:
        """
        Execute a registered tool and return its result.

        Raises ValueError if the tool is not registered.
        """
        tool = self.get(tool_name)
        if tool is None:
            raise ValueError(
                f"Tool '{tool_name}' is not registered. "
                f"Available: {self.list_names()}"
            )
        logger.debug("Executing custom tool '%s' with args: %s", tool_name, args)
        return tool.handler(args)


# ---------------------------------------------------------------------------
# Detect if an agent response contains a tool-call signal
# ---------------------------------------------------------------------------

_TOOL_CALL_PATTERN = re.compile(
    r'"tool_call"\s*:\s*\{|'  # {"tool_call": {...}}
    r'"tool"\s*:\s*"(\w+)"|'  # {"tool": "name"}
    r'<tool_call>',            # XML-style tool call
    re.IGNORECASE,
)


def response_contains_tool_call(text: str) -> bool:
    """
    Return True if the raw agent text looks like it contains a tool call.

    Claude CLI handles tool calls internally -- this is a diagnostic helper
    for debugging and testing.  It should not trigger in normal operation
    because the CLI resolves tool calls before returning final text.
    """
    return bool(_TOOL_CALL_PATTERN.search(text))


# ---------------------------------------------------------------------------
# Module-level default registry (shared across the process)
# ---------------------------------------------------------------------------

default_registry = ToolRegistry()
