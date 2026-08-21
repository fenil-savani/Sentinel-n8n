"""
settings.py -- Central configuration for claude-cli-orchestrator.

All paths, constants, and environment-level settings live here.
No business logic -- import this module from anywhere.
"""

import importlib.util
import os
import platform
import shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root (absolute, derived from this file's location)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent   # claude-cli-orchestrator/

# ---------------------------------------------------------------------------
# Claude CLI binary -- cross-platform resolution
#
# Resolution order (first match wins):
#   1. CLAUDE_BIN env var          -- explicit override for any non-standard install
#   2. claude_agent_sdk._bundled/  -- package-bundled binary (pip install)
#   3. shutil.which("claude")      -- system PATH (npm install, brew, manual)
#
# Works on: Windows, macOS, Linux
# Python version independent -- uses importlib, not a hardcoded path.
# ---------------------------------------------------------------------------

def _resolve_claude_bin() -> str:
    """
    Return the absolute path to the claude CLI binary.

    Checks (in order):
      1. CLAUDE_BIN environment variable -- set this for non-standard installs.
      2. claude_agent_sdk package _bundled/ directory -- pip-installed bundle.
      3. System PATH via shutil.which -- handles npm/brew/manual installs and
         automatically appends .exe / .cmd on Windows.

    Raises FileNotFoundError with actionable instructions if none found.
    """
    # --- Layer 1: explicit env-var override (works everywhere) ---------------
    env_bin = os.environ.get("CLAUDE_BIN", "").strip()
    if env_bin:
        p = Path(env_bin)
        if p.exists():
            return str(p)
        raise FileNotFoundError(
            f"CLAUDE_BIN is set to '{env_bin}' but that path does not exist."
        )

    # Binary filename differs on Windows
    bin_name = "claude.exe" if platform.system() == "Windows" else "claude"

    # --- Layer 2: bundled binary inside the claude_agent_sdk package ---------
    # importlib.find_spec locates the package regardless of Python version,
    # venv, conda, pyenv, system install, or OS.
    spec = importlib.util.find_spec("claude_agent_sdk")
    if spec and spec.origin:
        bundled = Path(spec.origin).parent / "_bundled" / bin_name
        if bundled.exists():
            return str(bundled)

    # --- Layer 3: PATH lookup (npm, brew, manual, claude-code installer) -----
    found = shutil.which("claude")
    if found:
        return found

    # --- Nothing found -- give actionable instructions ------------------------
    raise FileNotFoundError(
        "claude CLI binary not found.\n"
        "\n"
        "Tried (in order):\n"
        "  1. CLAUDE_BIN env var          -- not set\n"
        "  2. claude_agent_sdk._bundled/  -- package not installed or binary absent\n"
        "  3. PATH lookup (shutil.which)  -- 'claude' not on PATH\n"
        "\n"
        "Fix options:\n"
        "  A) Install Claude Code:  https://claude.ai/download  (adds 'claude' to PATH)\n"
        "  B) Install via npm:      npm install -g @anthropic-ai/claude-code\n"
        "  C) Set env var:          export CLAUDE_BIN=/path/to/claude  (Linux/macOS)\n"
        "                           set CLAUDE_BIN=C:\\path\\to\\claude.exe  (Windows)\n"
    )


CLAUDE_BIN: str = _resolve_claude_bin()

# ---------------------------------------------------------------------------
# Default model
# ---------------------------------------------------------------------------
DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# ---------------------------------------------------------------------------
# Directory paths
# ---------------------------------------------------------------------------
AGENTS_DIR        = PROJECT_ROOT / "agents"
PRODUCT_DOCS_DIR  = PROJECT_ROOT / "product_docs"
MCP_CONFIG_PATH   = PROJECT_ROOT / "mcp" / "mcp.json"
SKILLS_DIR        = PROJECT_ROOT / "skills"
RUNS_DIR          = PROJECT_ROOT / "runs"
PROJECT_CONFIG_PATH = PROJECT_ROOT / "project.yaml"

RUNS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Agent definitions
#
# Each entry maps a logical agent name to:
#   prompt_file  -- filename inside AGENTS_DIR
#   tools        -- tools passed to --allowedTools
# ---------------------------------------------------------------------------
AGENT_DEFINITIONS = {
    "generator": {
        "prompt_file": "generator.md",
        "tools": [
            "Read",
            "Grep",
            # KAPA context engine -- provides rich platform syntax docs dynamically.
            # Falls back to product_docs/ file reads when the server is unavailable.
            # "mcp__context-engine__context_engine_agent",  # disabled: server temporarily unavailable
        ],
    },
    "reviewer": {
        "prompt_file": "reviewer.md",
        "tools": [
            "Read",
            "Grep",
            # MCP validation tools for V5 syntax checking (google-secops-mcp-server required)
            "mcp__google-secops-mcp-server__validate_dashboard_query",
            "mcp__google-secops-mcp-server__validate_rule",
        ],
    },
}

# ---------------------------------------------------------------------------
# Orchestrator constants
# ---------------------------------------------------------------------------
# MAX_ITERATIONS is now defined per-project in project.yaml (workflow.max_iterations).
# Kept here only as a fallback for dry-run checks and tests.
MAX_ITERATIONS  = 3
AGENT_TIMEOUT   = 1200        # Seconds per agent call (20 min)

# ---------------------------------------------------------------------------
# Known Claude models -- used for --model validation warning in main.py.
# Update when new models are released.
# ---------------------------------------------------------------------------
KNOWN_MODELS = {
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")   # DEBUG | INFO | WARNING
VERBOSE   = os.environ.get("VERBOSE", "0") == "1"
