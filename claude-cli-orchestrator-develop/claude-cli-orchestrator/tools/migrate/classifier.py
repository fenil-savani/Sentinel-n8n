"""
classifier.py -- Step 3 of the migration pipeline.

Heuristic pattern classifier. Takes a supervisor.md + the number of subagents
and returns one of:

  A -- single agent, runs once (no retry loop)
  B -- pipeline-level loop: multiple agents, one global exit condition,
       reviewer-failure re-runs the generator
  C -- step-level retries: each agent has its own independent retry loop

The classifier is heuristic and intentionally non-authoritative. Its output
is passed to the LLM extractor as a HINT. The LLM may disagree, and the
assembler trusts the LLM's final verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ClassificationResult:
    pattern:    str                # "A" | "B" | "C" | "unknown"
    confidence: float              # 0.0 - 1.0
    signals:    list[str] = field(default_factory=list)  # human-readable reasons
    iteration_hint: int | None = None   # e.g., extracted "3" from "current_iteration <= 3"


# ---------------------------------------------------------------------------
# Heuristic rules
# ---------------------------------------------------------------------------

# Pipeline-loop signals: a single WHILE/loop spans multiple phases.
PATTERN_B_SIGNALS = [
    re.compile(r"\bWHILE\b.*iteration\b",             re.IGNORECASE),
    re.compile(r"current_iteration\s*[<>=]+\s*\d+",   re.IGNORECASE),
    re.compile(r"loop\s+until\s+PASS",                re.IGNORECASE),
    re.compile(r"return\s+to\s+(Phase\s*1|Step\s*1)", re.IGNORECASE),
    re.compile(r"Iteration\s+Loop\b",                 re.IGNORECASE),
    re.compile(r"feedback_for_generator",             re.IGNORECASE),
    re.compile(r"validation_feedback",                re.IGNORECASE),
]

# Step-level-retry signals: per-phase retries, each with its own status check.
# Field-mapper's supervisor has two distinct "Iteration Loop on X failure" sections.
PATTERN_C_SIGNALS = [
    re.compile(r"Iteration\s+Loop\s+on\s+[\w\s]+failure", re.IGNORECASE),
    re.compile(r"REPEAT\s+until\s+(both|status|the\s+\w+)",    re.IGNORECASE),
    re.compile(r"Delegate\s+to\s+`?\w+`?\s+again",            re.IGNORECASE),
]

# Iteration-count extractor: "3 total iterations", "max 3 iterations", "<= 3"
ITERATION_COUNT_PATTERNS = [
    re.compile(r"(\d+)\s+total\s+iterations",              re.IGNORECASE),
    re.compile(r"max(?:imum)?\s+of?\s+(\d+)\s+iterations", re.IGNORECASE),
    re.compile(r"current_iteration\s*<=?\s*(\d+)",         re.IGNORECASE),
    re.compile(r"up\s+to\s+(\d+)\s+(?:retries|iterations)", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class Classifier:
    """Heuristic pattern classifier. No LLM, no external calls."""

    def classify(
        self,
        supervisor_md: Path | str,
        num_subagents: int,
    ) -> ClassificationResult:
        text = self._load(supervisor_md)

        b_hits = self._count_matches(text, PATTERN_B_SIGNALS)
        c_hits = self._count_matches(text, PATTERN_C_SIGNALS)
        iteration_hint = self._extract_iteration_count(text)

        signals: list[str] = []
        signals.append(f"num_subagents={num_subagents}")
        signals.append(f"pattern_B_hits={len(b_hits)}")
        signals.append(f"pattern_C_hits={len(c_hits)}")
        if iteration_hint is not None:
            signals.append(f"iteration_hint={iteration_hint}")

        # --- Decision tree ---
        # 1 subagent AND no pipeline-loop signals -> A
        if num_subagents <= 1 and len(b_hits) == 0:
            return ClassificationResult(
                pattern="A",
                confidence=0.9,
                signals=signals + ["single subagent, no loop keywords"],
                iteration_hint=iteration_hint,
            )

        # 2+ subagents -> weigh B vs C signals.
        # Pattern C is recognized by multiple per-phase retry loops.
        if len(c_hits) >= 2 and len(c_hits) > len(b_hits):
            return ClassificationResult(
                pattern="C",
                confidence=0.85,
                signals=signals + [
                    f"multiple per-phase retry signals matched: {[p.pattern for p in c_hits]}"
                ],
                iteration_hint=iteration_hint,
            )

        # Pipeline loop: a global WHILE or explicit reviewer-feedback flow.
        if len(b_hits) >= 2:
            return ClassificationResult(
                pattern="B",
                confidence=0.85,
                signals=signals + [
                    f"pipeline-loop signals matched: {[p.pattern for p in b_hits]}"
                ],
                iteration_hint=iteration_hint,
            )

        # Ambiguous: signals weak in both directions.
        if num_subagents >= 2:
            return ClassificationResult(
                pattern="B",
                confidence=0.5,
                signals=signals + ["weak signals; defaulting to B for multi-agent"],
                iteration_hint=iteration_hint,
            )

        return ClassificationResult(
            pattern="unknown",
            confidence=0.0,
            signals=signals + ["no matching heuristic"],
            iteration_hint=iteration_hint,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load(source: Path | str) -> str:
        if isinstance(source, Path):
            return source.read_text(encoding="utf-8", errors="replace")
        return source

    @staticmethod
    def _count_matches(text: str, patterns: list[re.Pattern]) -> list[re.Pattern]:
        return [p for p in patterns if p.search(text)]

    @staticmethod
    def _extract_iteration_count(text: str) -> int | None:
        for p in ITERATION_COUNT_PATTERNS:
            m = p.search(text)
            if m:
                try:
                    return int(m.group(1))
                except (ValueError, IndexError):
                    continue
        return None
