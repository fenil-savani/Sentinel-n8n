#!/usr/bin/env python
"""
test_claude_cli.py -- Standalone Claude CLI tester.

Sends a prompt to Claude CLI (with optional MCP config and tools) and prints
every field returned: result text, cost, turns, session ID, elapsed time, and
the full raw JSON envelope.

Works on: Windows, macOS, Linux (Claude CLI binary resolved automatically).

Usage
-----
# Basic prompt:
  python test_claude_cli.py "What is 2+2?"

# With a specific model:
  python test_claude_cli.py "What is 2+2?" --model claude-haiku-4-5-20251001

# With MCP tools:
  python test_claude_cli.py "Call context_engine_agent with query: YARA-L events syntax" \
      --tools mcp__context-engine__context_engine_agent \
      --mcp-config mcp/mcp.json

# Multiple tools:
  python test_claude_cli.py "Read the file agents/generator.md" \
      --tools Read,Grep \
      --mcp-config mcp/mcp.json

# Verbose (show full JSON envelope):
  python test_claude_cli.py "Hello" --verbose
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from config.settings import _resolve_claude_bin

# ---------------------------------------------------------------------------
# Resolve claude binary (OS-agnostic: Windows, macOS, Linux)
# ---------------------------------------------------------------------------

try:
    CLAUDE_BIN = _resolve_claude_bin()
except FileNotFoundError as exc:
    print(f"ERROR: {exc}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run_claude(
    prompt: str,
    model: str = "claude-sonnet-4-6",
    tools: list[str] | None = None,
    mcp_config: str | None = None,
    system_prompt: str | None = None,
    verbose: bool = False,
) -> dict:
    """
    Invoke Claude CLI with the given prompt and return a result dict with fields:
      success, result, cost_usd, num_turns, session_id, elapsed_sec, raw_envelope
    """
    cmd = [
        CLAUDE_BIN,
        "--print",
        "--output-format", "json",
        "--model", model,
    ]

    if tools:
        cmd += ["--allowedTools", ",".join(tools)]

    if mcp_config:
        mcp_path = Path(mcp_config)
        if not mcp_path.exists():
            raise FileNotFoundError(f"MCP config not found: {mcp_path}")
        cmd += ["--mcp-config", str(mcp_path)]

    if system_prompt:
        cmd += ["--system-prompt", system_prompt]

    if verbose:
        print(f"\n[CMD] {' '.join(cmd[:8])} ... (+ prompt via stdin)")

    user_message = json.dumps([{"role": "user", "content": prompt}])

    t0 = time.monotonic()
    proc = subprocess.run(
        cmd,
        input=user_message,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = time.monotonic() - t0

    if verbose:
        print(f"[EXIT] code={proc.returncode}  elapsed={elapsed:.2f}s")
        if proc.stderr.strip():
            print(f"[STDERR]\n{proc.stderr.strip()}")

    # Parse JSON envelope
    raw_envelope: dict = {}
    if proc.stdout.strip():
        try:
            raw_envelope = json.loads(proc.stdout.strip())
        except json.JSONDecodeError as exc:
            # Claude sometimes emits plain text on error
            raw_envelope = {"raw_stdout": proc.stdout.strip(), "parse_error": str(exc)}

    # Extract standard fields
    success    = proc.returncode == 0 and "result" in raw_envelope
    result     = raw_envelope.get("result", raw_envelope.get("raw_stdout", ""))
    cost_usd   = raw_envelope.get("total_cost_usd", raw_envelope.get("cost_usd", 0.0))
    num_turns  = raw_envelope.get("num_turns", 0)
    session_id = raw_envelope.get("session_id", "")

    return {
        "success":      success,
        "result":       result,
        "cost_usd":     cost_usd,
        "num_turns":    num_turns,
        "session_id":   session_id,
        "elapsed_sec":  round(elapsed, 3),
        "return_code":  proc.returncode,
        "raw_envelope": raw_envelope,
    }


# ---------------------------------------------------------------------------
# Pretty printer
# ---------------------------------------------------------------------------

def print_result(r: dict, verbose: bool) -> None:
    width = 72
    status = "SUCCESS" if r["success"] else "FAILED"
    print("\n" + "=" * width)
    print(f"  Status:      {status}")
    print(f"  Model call:  {r['num_turns']} turn(s)")
    print(f"  Cost:        ${r['cost_usd']:.6f}")
    print(f"  Elapsed:     {r['elapsed_sec']:.2f}s")
    print(f"  Session ID:  {r['session_id'] or '(none)'}")
    print(f"  Return code: {r['return_code']}")
    print("=" * width)

    print("\n--- Result ---")
    print(r["result"])
    print("-" * width)

    if verbose:
        print("\n--- Raw JSON Envelope ---")
        envelope_copy = {k: v for k, v in r["raw_envelope"].items() if k != "result"}
        print(json.dumps(envelope_copy, indent=2, ensure_ascii=False, default=str))
        print("-" * width)

    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Standalone Claude CLI tester -- send a prompt and print all output fields.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("prompt", help="Prompt to send to Claude.")
    p.add_argument(
        "--model", "-m",
        default="claude-sonnet-4-6",
        help="Claude model to use (default: claude-sonnet-4-6).",
    )
    p.add_argument(
        "--tools", "-t",
        default=None,
        help="Comma-separated list of allowed tools (e.g. Read,Grep,mcp__server__tool).",
    )
    p.add_argument(
        "--mcp-config",
        default=None,
        metavar="FILE",
        help="Path to MCP config JSON file (required when using MCP tools).",
    )
    p.add_argument(
        "--system-prompt", "-s",
        default=None,
        metavar="TEXT",
        help="Optional system prompt.",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print the full raw JSON envelope and CLI command.",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()

    tools = [t.strip() for t in args.tools.split(",")] if args.tools else None

    mcp_config = args.mcp_config
    # Default to project mcp.json if tools reference MCP but no config given
    if tools and any("mcp__" in t for t in tools) and not mcp_config:
        default_mcp = Path(__file__).parent / "mcp" / "mcp.json"
        if default_mcp.exists():
            mcp_config = str(default_mcp)
            print(f"[INFO] Using default MCP config: {mcp_config}")

    print(f"\nSending prompt to Claude CLI ({args.model})...")
    if tools:
        print(f"Tools: {', '.join(tools)}")

    try:
        result = run_claude(
            prompt=args.prompt,
            model=args.model,
            tools=tools,
            mcp_config=mcp_config,
            system_prompt=args.system_prompt,
            verbose=args.verbose,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print_result(result, verbose=args.verbose)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())