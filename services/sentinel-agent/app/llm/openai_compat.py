"""OpenAI chat-completions-compatible proxy, backed by the `claude` CLI.

Lets n8n's own orchestrator chat model run on a Claude Code subscription too,
not just sentinel-agent's generation calls. n8n's native Anthropic Chat Model
node has no "point at a local CLI" option, but its OpenAI Chat Model node
does accept an arbitrary base URL — confirmed by reading n8n's actual
installed source: LmChatOpenAi.node.js instantiates LangChain's ChatOpenAI,
which POSTs standard OpenAI request/response shapes to
`<baseURL>/chat/completions`, with genuine OpenAI tool-calling
(`tools`/`tool_choice`, `choices[0].message.tool_calls`) and no n8n-specific
extensions. See app/main.py's /v1/chat/completions for the HTTP side.

Design notes:

* Every step of n8n's own tool-calling loop becomes one `claude --print`
  subprocess call here — there is no cheaper way to get a single completion
  out of the CLI. This is slower per turn than a direct Anthropic API call;
  that trade-off was made deliberately in exchange for not needing an API
  key for this node. Do not try to "optimize" this into a persistent
  session — see the module docstring in app/llm/claude_cli.py for why a
  `--resume`-based loop was already rejected for the generation path, for
  the same underlying reason (detecting tool intent from free-form
  multi-turn state is exactly what --json-schema avoids).

* OpenAI's chat-completions API is stateless per call — the full message
  history arrives on every request. That matches `claude --print`'s
  one-shot model well: no session/--resume juggling needed, the entire
  conversation is just serialized into one user-turn transcript each time.

* The response schema is a single flat object with a loose `arguments`
  object, not a discriminated union of exact per-tool schemas. Two reasons:
  Anthropic's tool input_schema rejects `oneOf` at the top level (proven
  live against the API — see claude_cli.py), and the incoming tool set here
  is arbitrary (whatever n8n's ai_tool-connected nodes are), so a flat
  merged-properties schema would risk name collisions across unrelated
  tools. Precision comes from describing each tool's real parameter schema
  in the system prompt text — the same way tool use normally works — not
  from JSON Schema validation of the arguments themselves.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from . import cli_exec

log = logging.getLogger(__name__)

_RESPOND = "__respond__"


def build_system_prompt(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> str:
    system_text = "\n\n".join(
        str(m.get("content") or "") for m in messages if m.get("role") == "system"
    )

    catalog_lines = []
    for t in tools or []:
        fn = t.get("function") or {}
        name = fn.get("name", "")
        if not name:
            continue
        description = fn.get("description", "")
        parameters = fn.get("parameters") or {}
        catalog_lines.append(f"- `{name}`: {description}\n  Parameters (JSON Schema): {json.dumps(parameters)}")
    catalog = "\n".join(catalog_lines) if catalog_lines else "(no tools available in this turn)"

    addendum = (
        "\n\n---\n\n"
        "# Harness notes (appended — how to respond in this environment)\n\n"
        "You have no native tool-calling here. Instead, respond via the structured "
        "output schema you've been given, using these fields:\n\n"
        f"- `tool`: the name of the tool to call, or `{_RESPOND}` if you're replying to "
        "the analyst directly instead of calling a tool.\n"
        "- `arguments`: an object with that tool's arguments, matching its documented "
        f"parameter schema below. Omit or leave empty when tool is `{_RESPOND}`.\n"
        "- `content`: your plain-text reply to the analyst. Required when tool is "
        f"`{_RESPOND}`; optional otherwise.\n\n"
        f"Available tools:\n{catalog}"
    )
    return system_text + addendum


def build_transcript(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        if role == "user":
            lines.append(f"User: {m.get('content') or ''}")
        elif role == "assistant":
            content = m.get("content")
            if content:
                lines.append(f"Assistant: {content}")
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                lines.append(f"Assistant called tool `{fn.get('name')}` with arguments: {fn.get('arguments')}")
        elif role == "tool":
            lines.append(f"Tool result: {m.get('content') or ''}")
        elif m.get("content"):
            lines.append(f"{role}: {m.get('content')}")

    lines.append(
        "\nRespond now, using the structured output format described in your "
        "instructions above — pick a `tool` (or `__respond__`) and fill in "
        "`arguments`/`content` accordingly."
    )
    return "\n".join(lines)


def build_schema(tools: list[dict[str, Any]]) -> dict[str, Any]:
    names = [n for t in (tools or []) if (n := (t.get("function") or {}).get("name"))]
    return {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": [*names, _RESPOND]},
            "arguments": {
                "type": "object",
                "description": "Arguments for the chosen tool, per its documented schema. "
                               f"Omit when tool is {_RESPOND}.",
            },
            "content": {
                "type": "string",
                "description": f"Plain text reply to the analyst. Required when tool is {_RESPOND}.",
            },
        },
        "required": ["tool"],
        "additionalProperties": False,
    }


def to_openai_response(
    *, payload: dict[str, Any] | None, text: str, model: str, usage: dict[str, int]
) -> dict[str, Any]:
    tool_name = payload.get("tool") if isinstance(payload, dict) else None

    if tool_name and tool_name != _RESPOND:
        arguments = (payload.get("arguments") or {}) if isinstance(payload, dict) else {}
        message = {
            "role": "assistant",
            "content": payload.get("content") if isinstance(payload, dict) else None,
            "tool_calls": [{
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {"name": tool_name, "arguments": json.dumps(arguments)},
            }],
        }
        finish_reason = "tool_calls"
    else:
        content = payload.get("content") if isinstance(payload, dict) else None
        # Fall back to the raw text if the model didn't fill `content` (or
        # structured output failed to parse at all) — a plain-text reply the
        # analyst can read beats erroring the whole chat turn.
        message = {"role": "assistant", "content": content if content is not None else text}
        finish_reason = "stop"

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage,
    }


async def handle(body: dict[str, Any], settings: Any) -> dict[str, Any]:
    messages = body.get("messages") or []
    tools = body.get("tools") or []

    envelope, tool_trace = await cli_exec.run_claude_once(
        bin_path=settings.claude_cli_bin,
        oauth_token=settings.claude_code_oauth_token,
        timeout=settings.claude_cli_timeout,
        system=build_system_prompt(messages, tools),
        user_message=build_transcript(messages),
        model=settings.orchestrator_model,
        schema=build_schema(tools),
        add_dir=None,  # the orchestrator's chat model needs no file access
    )

    text = envelope.get("result") or ""
    payload = envelope.get("structured_output")
    if payload is None and text:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None

    usage_raw = envelope.get("usage") or {}
    prompt_tokens = usage_raw.get("input_tokens", 0)
    completion_tokens = usage_raw.get("output_tokens", 0)
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }

    log.info(
        "openai_compat OK | session=%s turns=%s cost=$%.4f tool=%s native_tools=%s",
        envelope.get("session_id"), envelope.get("num_turns", 0),
        envelope.get("total_cost_usd", 0.0),
        payload.get("tool") if isinstance(payload, dict) else None,
        tool_trace,
    )

    return to_openai_response(payload=payload, text=text, model=settings.orchestrator_model, usage=usage)
