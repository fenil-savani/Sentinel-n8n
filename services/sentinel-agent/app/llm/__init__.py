"""Runtime factory."""

from __future__ import annotations

import logging

from ..config import Settings
from .anthropic import AnthropicRuntime
from .base import AgentRuntime, LLMUnavailable, RunResult, Tool, ToolExecutionError
from .claude_cli import ClaudeCliRuntime

log = logging.getLogger(__name__)

__all__ = [
    "AgentRuntime",
    "LLMUnavailable",
    "RunResult",
    "Tool",
    "ToolExecutionError",
    "build_runtime",
]


def build_runtime(settings: Settings, *, max_tokens: int = 16000) -> AgentRuntime:
    if settings.llm_provider == "claude_cli":
        log.info(
            "build_runtime: provider=claude_cli bin=%s timeout=%ss "
            "(max_tokens=%d requested but has no effect on this provider)",
            settings.claude_cli_bin or "<resolved via PATH>",
            settings.claude_cli_timeout, max_tokens,
        )
        return ClaudeCliRuntime(
            reference_dir=settings.reference_dir,
            bin_path=settings.claude_cli_bin,
            oauth_token=settings.claude_code_oauth_token,
            timeout=settings.claude_cli_timeout,
        )
    if settings.llm_provider != "anthropic":
        log.warning(
            "build_runtime: unknown LLM_PROVIDER=%r, falling back to 'anthropic'",
            settings.llm_provider,
        )
    log.info("build_runtime: provider=anthropic max_tokens=%d", max_tokens)
    return AnthropicRuntime(settings.anthropic_api_key, max_tokens=max_tokens)
