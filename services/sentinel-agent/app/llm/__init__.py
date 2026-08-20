"""Runtime factory."""

from __future__ import annotations

from ..config import Settings
from .anthropic import AnthropicRuntime
from .base import AgentRuntime, LLMUnavailable, RunResult, Tool, ToolExecutionError

__all__ = [
    "AgentRuntime",
    "LLMUnavailable",
    "RunResult",
    "Tool",
    "ToolExecutionError",
    "build_runtime",
]


def build_runtime(settings: Settings, *, max_tokens: int = 16000) -> AgentRuntime:
    return AnthropicRuntime(settings.anthropic_api_key, max_tokens=max_tokens)
