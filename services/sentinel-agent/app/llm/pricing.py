"""Token usage normalization and cost computation.

Kept separate from the provider modules (`anthropic.py`, `claude_cli.py`) so
both can share one `Usage` shape and one pricing table, even though they read
usage off two structurally different sources (an SDK response object vs. a
CLI's NDJSON envelope dict).

Cost is always computed from `_RATES` below, for both providers — the Claude
CLI's own self-reported `total_cost_usd` is captured (`Usage.
provider_reported_cost_usd`) but never used in an aggregate, since it may
reflect subscription-plan accounting rather than marginal API cost and would
make a `SUM(cost_usd)` across providers mix methodologies invisibly.

Rates are current as of 2026-08-30 (see the `claude-api` skill / Anthropic's
pricing page for the source of truth). This table is a manual, infrequent
chore, not an attempt at auto-fetching prices — Anthropic changes list prices
rarely enough that this is the right tradeoff.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_5m_tokens: int = 0
    cache_creation_1h_tokens: int = 0
    cache_read_tokens: int = 0
    #: The CLI's own opaque `total_cost_usd`, when available. Cross-check
    #: column only — never summed alongside `cost_usd` in an aggregate.
    provider_reported_cost_usd: float | None = None

    def __iadd__(self, other: "Usage") -> "Usage":
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_5m_tokens += other.cache_creation_5m_tokens
        self.cache_creation_1h_tokens += other.cache_creation_1h_tokens
        self.cache_read_tokens += other.cache_read_tokens
        return self


@dataclass(frozen=True, slots=True)
class ModelRate:
    input_per_mtok: float
    output_per_mtok: float
    #: Overrides for the cache multipliers below, only if a model's actual
    #: rate diverges from the standard ratios — none currently do.
    cache_write_5m_per_mtok: float | None = None
    cache_write_1h_per_mtok: float | None = None
    cache_read_per_mtok: float | None = None


# Keyed by model-family prefix, not exact dated id, so a deployed model string
# like "claude-haiku-4-5-20251001" matches "claude-haiku-4-5" without needing
# an update every time a new dated snapshot ships (same prefix-match pattern
# `anthropic.py`'s `_FALLBACK_MODELS` already uses).
_RATES: dict[str, ModelRate] = {
    "claude-haiku-4-5": ModelRate(input_per_mtok=1.00, output_per_mtok=5.00),
    "claude-sonnet-5": ModelRate(input_per_mtok=2.00, output_per_mtok=10.00),
    "claude-opus-5": ModelRate(input_per_mtok=5.00, output_per_mtok=25.00),
}


def rate_for(model: str) -> ModelRate | None:
    for prefix, rate in _RATES.items():
        if model.startswith(prefix):
            return rate
    return None


def compute_cost_usd(model: str, usage: Usage) -> float | None:
    """`None` for an unrecognized model — the caller should log a warning and
    store NULL rather than guess at a rate."""
    rate = rate_for(model)
    if rate is None:
        return None
    write_5m = rate.cache_write_5m_per_mtok
    if write_5m is None:
        write_5m = rate.input_per_mtok * 1.25
    write_1h = rate.cache_write_1h_per_mtok
    if write_1h is None:
        write_1h = rate.input_per_mtok * 2.0
    read = rate.cache_read_per_mtok
    if read is None:
        read = rate.input_per_mtok * 0.1

    return (
        usage.input_tokens * rate.input_per_mtok
        + usage.output_tokens * rate.output_per_mtok
        + usage.cache_creation_5m_tokens * write_5m
        + usage.cache_creation_1h_tokens * write_1h
        + usage.cache_read_tokens * read
    ) / 1_000_000
