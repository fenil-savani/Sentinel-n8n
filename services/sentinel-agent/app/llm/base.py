"""Agent loop contract.

`Tool` and `RunResult` are kept separate from `AnthropicRuntime` itself so the
tool-calling loop's shape — system prompt, tools, terminal-tool exit — stays
one clear interface even though there is a single implementation.

Terminal tools are how a run ends deliberately. Rather than parsing an
artifact out of free-form model prose (fenced blocks, preambles, apologies),
the model is told to call e.g. `submit_parser(yaml=...)`. The runtime captures
the arguments and stops. This is markedly more reliable with small local
models, which are prone to wrapping output in commentary.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


class LLMUnavailable(RuntimeError):
    """Backend unreachable, timed out, or refused. Surfaces as HTTP 503 —
    distinct from a generation failure, which is a 200 with a failed draft."""


class ToolExecutionError(RuntimeError):
    """A tool raised. The message is fed back to the model so it can adapt,
    rather than aborting the run."""


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    #: JSON Schema for the argument object, in Anthropic's input_schema shape.
    parameters: dict[str, Any]
    #: Executed and its return value fed back. ``None`` for terminal tools.
    fn: Callable[..., Any] | None = None
    #: Calling this ends the run; its arguments become ``RunResult.payload``.
    terminal: bool = False


@dataclass(slots=True)
class RunResult:
    #: Name of the terminal tool the model called, or None if it just stopped.
    terminal_tool: str | None
    #: Arguments of that terminal tool.
    payload: dict[str, Any] | None
    #: Final assistant text (usually empty when a terminal tool was used).
    text: str
    iterations: int
    #: Names of non-terminal tools called, in order. Useful for debugging a
    #: model that never reads the reference file it was told to read.
    tool_trace: list[str] = field(default_factory=list)


class AgentRuntime(ABC):
    """One tool-calling loop, one provider."""

    @abstractmethod
    async def run(
        self,
        *,
        system: str,
        user: str,
        tools: list[Tool],
        model: str,
        max_iterations: int = 24,
    ) -> RunResult: ...

    @abstractmethod
    async def aclose(self) -> None: ...

    def supported_tool_names(self, tools: list[Tool]) -> list[str]:
        """Names of `tools` this runtime can actually expose to the model.

        Used to build a harness prompt that doesn't claim capabilities the
        runtime doesn't have (see prompts.py's ``harness()``). A runtime that
        executes every tool it's given (e.g. AnthropicRuntime) just returns
        every name; one that can't act on non-terminal Python tools (e.g.
        ClaudeCliRuntime, whose CLI subprocess owns its own tool loop)
        overrides this to report only the subset it truly supports.
        """
        return [t.name for t in tools]

    # ── shared helpers ───────────────────────────────────────────────────

    @staticmethod
    def _index(tools: list[Tool]) -> dict[str, Tool]:
        return {t.name: t for t in tools}

    @staticmethod
    async def _execute(tool: Tool, args: dict[str, Any]) -> str:
        """Run a non-terminal tool, coercing any failure into a string the
        model can read. A tool that raises must not kill the run — the model
        is usually able to correct course (bad path, malformed KQL) when told
        what went wrong."""
        import inspect

        if tool.fn is None:  # pragma: no cover - guarded by caller
            return "ERROR: tool has no implementation"
        try:
            result = tool.fn(**args)
            if inspect.isawaitable(result):
                result = await result
            return result if isinstance(result, str) else str(result)
        except TypeError as exc:
            # Almost always the model inventing or omitting an argument.
            return f"ERROR: bad arguments for {tool.name}: {exc}"
        except Exception as exc:  # noqa: BLE001 - deliberate: feed back, don't crash
            return f"ERROR: {tool.name} failed: {exc}"
