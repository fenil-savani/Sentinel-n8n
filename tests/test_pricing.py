"""LLM token/cost tracking — no model or DB needed.

    pytest tests/test_pricing.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "services" / "sentinel-agent"))

from app.config import Settings  # noqa: E402
from app.llm.anthropic import AnthropicRuntime  # noqa: E402
from app.llm.base import LLMUnavailable, Tool  # noqa: E402
from app.llm.pricing import Usage, compute_cost_usd, rate_for  # noqa: E402


# ── pricing table ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "model,expected_rate",
    [
        ("claude-haiku-4-5-20251001", (1.00, 5.00)),
        ("claude-sonnet-5", (2.00, 10.00)),
        ("claude-opus-5", (5.00, 25.00)),
    ],
)
def test_rate_for_matches_dated_and_bare_ids(model: str, expected_rate: tuple[float, float]) -> None:
    rate = rate_for(model)
    assert rate is not None
    assert (rate.input_per_mtok, rate.output_per_mtok) == expected_rate


def test_rate_for_unknown_model_returns_none() -> None:
    assert rate_for("claude-nonexistent-9") is None


def test_compute_cost_usd_unknown_model_returns_none_not_a_guess() -> None:
    assert compute_cost_usd("claude-nonexistent-9", Usage(input_tokens=1000)) is None


def test_compute_cost_usd_input_output_only() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    # Haiku 4.5: $1/MTok in, $5/MTok out.
    assert compute_cost_usd("claude-haiku-4-5-20251001", usage) == pytest.approx(6.00)


def test_compute_cost_usd_cache_multipliers() -> None:
    # Opus 5: $5/MTok input. Cache write 1.25x (5m) / 2x (1h), cache read 0.1x.
    usage = Usage(
        cache_creation_5m_tokens=1_000_000,
        cache_creation_1h_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    cost = compute_cost_usd("claude-opus-5", usage)
    assert cost == pytest.approx(5.00 * 1.25 + 5.00 * 2.0 + 5.00 * 0.1)


def test_every_configured_model_is_priced() -> None:
    """Calibration: a future model swap this table doesn't know about must
    fail loudly here, not silently store NULL cost forever."""
    settings = Settings()
    for model in (
        settings.llm_model_parser,
        settings.llm_model_workbook_manifest,
        settings.llm_model_panel,
    ):
        assert rate_for(model) is not None, f"no pricing rate for configured model {model!r}"


# ── AnthropicRuntime.run() on_call instrumentation ──────────────────────────

class _FakeBlock:
    def __init__(self, type_: str, *, text: str = "", name: str = "", input: dict | None = None, id: str = "id1"):
        self.type = type_
        self.text = text
        self.name = name
        self.input = input or {}
        self.id = id


class _FakeUsage:
    def __init__(self, input_tokens: int = 10, output_tokens: int = 20):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 0
        self.cache_creation = None


class _FakeResponse:
    def __init__(self, *, stop_reason: str, content: list, usage: _FakeUsage | None = None):
        self.stop_reason = stop_reason
        self.content = content
        self.usage = usage or _FakeUsage()
        self.stop_details = None


@pytest.mark.asyncio
async def test_on_call_fires_once_per_iteration_until_terminal_tool() -> None:
    calls: list[int] = []

    async def on_call(usage, model, iteration, latency_ms):
        calls.append(iteration)

    runtime = AnthropicRuntime(api_key="unused")
    # Iteration 1: a non-terminal tool call. Iteration 2: the terminal tool.
    responses = [
        _FakeResponse(
            stop_reason="tool_use",
            content=[_FakeBlock("tool_use", name="lookup", input={}, id="a")],
        ),
        _FakeResponse(
            stop_reason="tool_use",
            content=[_FakeBlock("tool_use", name="submit_parser", input={"yaml": "x"}, id="b")],
        ),
    ]

    async def fake_create(**kwargs):
        return responses.pop(0)

    runtime._create = fake_create  # type: ignore[assignment]
    tools = [
        Tool(name="lookup", description="", parameters={"type": "object", "properties": {}},
             fn=lambda: "ok"),
        Tool(name="submit_parser", description="", parameters={"type": "object", "properties": {}},
             terminal=True),
    ]

    result = await runtime.run(system="sys", user="go", tools=tools, model="claude-haiku-4-5-20251001",
                                on_call=on_call)

    assert calls == [1, 2]
    assert result.terminal_tool == "submit_parser"
    assert result.usage.input_tokens == 20  # 10 + 10 across both iterations


@pytest.mark.asyncio
async def test_on_call_fires_for_every_attempt_even_on_max_iterations_exhaustion() -> None:
    """A run that never finishes must still have every attempted iteration's
    usage persisted — the failed/abandoned-run-still-gets-billed guarantee."""
    calls: list[int] = []

    async def on_call(usage, model, iteration, latency_ms):
        calls.append(iteration)

    runtime = AnthropicRuntime(api_key="unused")

    async def fake_create(**kwargs):
        # Always a non-terminal tool call — the loop never finds a terminal
        # tool and must exhaust max_iterations.
        return _FakeResponse(
            stop_reason="tool_use",
            content=[_FakeBlock("tool_use", name="lookup", input={}, id="a")],
        )

    runtime._create = fake_create  # type: ignore[assignment]
    tools = [
        Tool(name="lookup", description="", parameters={"type": "object", "properties": {}},
             fn=lambda: "ok"),
    ]

    with pytest.raises(LLMUnavailable):
        await runtime.run(system="sys", user="go", tools=tools, model="claude-haiku-4-5-20251001",
                           max_iterations=3, on_call=on_call)

    assert calls == [1, 2, 3]
