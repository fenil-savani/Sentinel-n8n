"""Anthropic tool-calling loop.

Deliberately a manual loop rather than ``client.beta.messages.tool_runner``.
The runner wants tools declared at import time via ``@beta_tool`` with typed
signatures; ours are built dynamically from a shared ``Tool`` dataclass instead,
so the same tool objects work whether they come from the parser skill, the
workbook manifest stage, or the per-panel loop.

Model-specific behaviour worth knowing before editing this file:

* ``temperature`` / ``top_p`` / ``top_k`` are **rejected with a 400** on Opus 5
  and Sonnet 5. Steer with the prompt instead.
* ``thinking.budget_tokens`` is removed; use ``thinking={"type": "adaptive"}``
  plus ``output_config.effort``.
* A response can come back HTTP 200 with ``stop_reason == "refusal"`` and empty
  content. Security tooling — which is exactly what this project generates —
  is the workload most likely to trip the cyber classifier, so refusals are
  handled explicitly and server-side fallbacks are enabled by default.
* The system prompt is the two skill files (6-13 KB) and is byte-identical
  across every call in a 100-panel run, so it carries a cache breakpoint.
  That is the difference between paying full input price 100 times and paying
  it once.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .base import AgentRuntime, LLMUnavailable, RunResult, Tool, UsageCallback
from .pricing import Usage

log = logging.getLogger(__name__)

#: Models that ship with safety classifiers where a server-side fallback is
#: worth having on by default.
_FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5", "claude-mythos-5")
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _usage_from_response(resp_usage: Any) -> Usage:
    """Normalize an Anthropic SDK response's `.usage` into our shared `Usage`
    shape. `cache_creation` (the 5m/1h TTL breakdown) is only present on
    newer API versions; when absent, conservatively bucket the whole write
    as 5-minute TTL rather than guess at a split."""
    cache_creation = getattr(resp_usage, "cache_creation", None)
    cache_5m = getattr(cache_creation, "ephemeral_5m_input_tokens", None) if cache_creation else None
    cache_1h = getattr(cache_creation, "ephemeral_1h_input_tokens", None) if cache_creation else None
    if cache_5m is None and cache_1h is None:
        cache_5m = getattr(resp_usage, "cache_creation_input_tokens", 0) or 0
        cache_1h = 0
    return Usage(
        input_tokens=getattr(resp_usage, "input_tokens", 0) or 0,
        output_tokens=getattr(resp_usage, "output_tokens", 0) or 0,
        cache_creation_5m_tokens=cache_5m or 0,
        cache_creation_1h_tokens=cache_1h or 0,
        cache_read_tokens=getattr(resp_usage, "cache_read_input_tokens", 0) or 0,
    )


class AnthropicRuntime(AgentRuntime):
    def __init__(self, api_key: str, effort: str = "high", max_tokens: int = 16000) -> None:
        # Deliberately lazy: this constructor runs during the sidecar's FastAPI
        # startup (see main.py's lifespan). Raising here on a missing key would
        # crash-loop the whole app the moment LLM_PROVIDER=anthropic is set,
        # even though every other endpoint (health, drafts, lint) needs no key
        # at all. Fail at the point of an actual generation call instead — the
        # same "degrade gracefully, error only when the feature is used"
        # pattern as azure_configured() elsewhere in this service.
        self._api_key = api_key
        self._client: Any = None
        self._effort = effort
        self._max_tokens = max_tokens

    def _ensure_client(self):
        if self._client is None:
            if not self._api_key:
                raise LLMUnavailable(
                    "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is unset. "
                    "Set it in .env and restart the sentinel-agent container."
                )
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()

    @staticmethod
    def _to_wire(tools: list[Tool]) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in tools
        ]

    async def _create(
        self,
        *,
        model: str,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        wire_tools: list[dict[str, Any]],
    ):
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": messages,
            "tools": wire_tools,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self._effort},
        }

        import anthropic

        client = self._ensure_client()
        try:
            if any(model.startswith(m) for m in _FALLBACK_MODELS):
                return await client.beta.messages.create(
                    betas=[_FALLBACK_BETA], fallbacks="default", **kwargs
                )
            return await client.messages.create(**kwargs)
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailable(f"Anthropic API unreachable: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMUnavailable(f"Anthropic rate limited: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMUnavailable(f"Anthropic returned {exc.status_code}: {exc.message}") from exc

    async def run(
        self,
        *,
        system: str,
        user: str,
        tools: list[Tool],
        model: str,
        max_iterations: int = 24,
        on_call: UsageCallback | None = None,
    ) -> RunResult:
        by_name = self._index(tools)
        wire_tools = self._to_wire(tools)

        # Cache breakpoint on the system prompt: stable across every call in a
        # run, so subsequent panels read it at ~0.1x instead of full price.
        system_blocks = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        trace: list[str] = []
        total_usage = Usage()

        for iteration in range(1, max_iterations + 1):
            started = time.monotonic()
            resp = await self._create(
                model=model, system=system_blocks, messages=messages, wire_tools=wire_tools
            )
            latency_ms = int((time.monotonic() - started) * 1000)

            # Usage is present on every response regardless of stop_reason —
            # read it before any refusal/content handling that might return
            # or raise early, so a refused or truncated turn still bills.
            call_usage = _usage_from_response(resp.usage)
            total_usage += call_usage
            if on_call is not None:
                await on_call(call_usage, model, iteration, latency_ms)

            # Always check stop_reason before touching content: on a refusal the
            # content array is empty and indexing it raises.
            if resp.stop_reason == "refusal":
                details = getattr(resp, "stop_details", None)
                category = getattr(details, "category", None) or "unspecified"
                raise LLMUnavailable(
                    f"Anthropic declined this request (category: {category}). "
                    "Security-adjacent content can trip the cyber classifier; "
                    "rephrase the request or use a different model."
                )

            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            tool_uses = [b for b in resp.content if b.type == "tool_use"]

            if resp.stop_reason == "max_tokens" and not tool_uses:
                raise LLMUnavailable(
                    f"response hit max_tokens ({self._max_tokens}) with no tool call; "
                    "the artifact is truncated"
                )

            if not tool_uses:
                return RunResult(
                    terminal_tool=None,
                    payload=None,
                    text=text,
                    iterations=iteration,
                    tool_trace=trace,
                    usage=total_usage,
                )

            # Echo the assistant turn back verbatim — thinking blocks and their
            # signatures must survive unmodified or the next turn 400s.
            messages.append({"role": "assistant", "content": resp.content})

            terminal = next((b for b in tool_uses if by_name[b.name].terminal), None)
            if terminal is not None:
                log.info("terminal tool %s after %d iteration(s)", terminal.name, iteration)
                return RunResult(
                    terminal_tool=terminal.name,
                    payload=dict(terminal.input),
                    text=text,
                    iterations=iteration,
                    tool_trace=trace,
                    usage=total_usage,
                )

            # All results for one assistant turn go back in a single user
            # message; splitting them trains the model out of parallel calls.
            results: list[dict[str, Any]] = []
            for block in tool_uses:
                tool = by_name.get(block.name)
                if tool is None:
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"ERROR: no tool named '{block.name}'",
                            "is_error": True,
                        }
                    )
                    continue
                trace.append(block.name)
                output = await self._execute(tool, dict(block.input))
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                        "is_error": output.startswith("ERROR:"),
                    }
                )
            messages.append({"role": "user", "content": results})

        raise LLMUnavailable(
            f"model did not finish within {max_iterations} iterations "
            f"(tools called: {', '.join(trace) or 'none'})"
        )
