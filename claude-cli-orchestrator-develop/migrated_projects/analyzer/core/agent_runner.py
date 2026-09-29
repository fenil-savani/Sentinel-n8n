"""
agent_runner.py -- Load an agent definition and execute it via Claude CLI.

Responsibilities
----------------
1. Read the agent's system prompt from its .md file (AGENTS_DIR)
2. Optionally inject skill context (via skill_loader)
3. Serialize the task payload to a JSON string (user message)
4. Delegate execution to ClaudeCliClient
5. Parse the JSON output from the agent's text response
6. Return a structured AgentResult

One AgentRunner instance can run any named agent (generator, reviewer, etc.)
by looking up the agent configuration from settings.AGENT_DEFINITIONS.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.settings import (
    AGENTS_DIR,
    AGENT_DEFINITIONS,
    AGENT_TIMEOUT,
    MCP_CONFIG_PATH,
    PROJECT_ROOT,
    SKILLS_DIR,
    VERBOSE,
)
from core.cli_client import ClaudeCliClient, ClaudeResponse
from core.skill_loader import inject_skills_into_prompt
from core.tool_executor import extract_json_from_text, JsonExtractionError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    """
    Outcome of a single agent invocation.

    Fields
    ------
    agent_name  : Which agent was run.
    success     : True if the agent returned parseable JSON output.
    output      : The parsed JSON dict (empty on failure).
    raw_text    : Raw text from the agent response.
    session_id  : Claude CLI session identifier for tracing.
    cost_usd    : Approximate API cost for this call.
    num_turns   : Number of internal tool-call turns the agent made.
    elapsed_sec : Wall-clock time for the full subprocess call.
    error       : Error message if success=False.
    input_tokens                : Prompt tokens billed (from envelope.usage).
    output_tokens               : Completion tokens billed.
    cache_read_input_tokens     : Tokens read from prompt cache.
    cache_creation_input_tokens : Tokens written to prompt cache.
    duration_ms                 : Total API round-trip time reported by Claude CLI.
    """
    agent_name:   str
    success:      bool
    output:       dict[str, Any]      = field(default_factory=dict)
    raw_text:     str                 = ""
    thinking:     str                 = ""
    session_id:   str                 = ""
    cost_usd:     float               = 0.0
    num_turns:    int                 = 0
    elapsed_sec:  float               = 0.0
    error:        str                 = ""
    input_tokens:                int  = 0
    output_tokens:               int  = 0
    cache_read_input_tokens:     int  = 0
    cache_creation_input_tokens: int  = 0
    duration_ms:                 int  = 0
    # Observability: which tools were called and which skills were injected
    tool_calls:    list[dict]         = field(default_factory=list)
    skills_loaded: list[str]          = field(default_factory=list)

    def __repr__(self) -> str:
        status = "OK" if self.success else f"FAIL({self.error[:60]})"
        return (
            f"<AgentResult agent={self.agent_name} {status} "
            f"turns={self.num_turns} cost=${self.cost_usd:.4f} "
            f"in={self.input_tokens} out={self.output_tokens} "
            f"elapsed={self.elapsed_sec:.1f}s>"
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class AgentRunner:
    """
    Executes a named agent by calling Claude CLI.

    Parameters
    ----------
    client : ClaudeCliClient
        Shared CLI client (model, timeout, verbose settings).
    load_skills : bool
        Whether to inject skill context into the system prompt.
    agent_definitions : dict | None
        Agent name -> {prompt_file, tools} mapping.
        When provided (e.g. loaded from project.yaml by WorkflowEngine),
        this overrides the global AGENT_DEFINITIONS from settings.py.
        Allows multiple projects to use the same AgentRunner class.
    """

    def __init__(
        self,
        client:             ClaudeCliClient | None = None,
        load_skills:        bool = True,
        agent_definitions:  dict | None = None,
    ):
        self.client             = client or ClaudeCliClient(timeout=AGENT_TIMEOUT)
        self.load_skills        = load_skills
        self.agent_definitions  = agent_definitions if agent_definitions is not None \
                                  else AGENT_DEFINITIONS
        # Cache only the base .md file content (never changes during a run).
        # Skill injection is payload-dependent and re-evaluated on every call.
        self._base_prompt_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        agent_name: str,
        task_payload: dict[str, Any],
        extra_system_context: str = "",
    ) -> AgentResult:
        """
        Run a named agent with the given task payload.

        Parameters
        ----------
        agent_name          : Must match a key in settings.AGENT_DEFINITIONS.
        task_payload        : Dict serialised as the user message (JSON).
        extra_system_context: Optional additional text appended to system prompt.

        Returns
        -------
        AgentResult -- always returns, never raises.
        The caller inspects .success and .error.
        """
        if agent_name not in self.agent_definitions:
            return AgentResult(
                agent_name=agent_name,
                success=False,
                error=f"Unknown agent '{agent_name}'. "
                      f"Available: {list(self.agent_definitions.keys())}",
            )

        cfg = self.agent_definitions[agent_name]

        try:
            system_prompt, skills_loaded = self._build_system_prompt(
                agent_name=agent_name,
                prompt_file=cfg["prompt_file"],
                extra_context=extra_system_context,
                payload=task_payload,
            )
            user_message = json.dumps(task_payload, indent=2, ensure_ascii=False)
            allowed_tools = cfg.get("tools", [])

            logger.info(
                "[%s] Starting | tools=%s payload_keys=%s",
                agent_name,
                allowed_tools,
                list(task_payload.keys()),
            )

            t0 = time.monotonic()
            cli_response: ClaudeResponse = self.client.run(
                system_prompt=system_prompt,
                user_message=user_message,
                allowed_tools=allowed_tools,
                mcp_config_path=MCP_CONFIG_PATH,  # Always pass MCP config (used when MCP tools are enabled)
                working_dir=PROJECT_ROOT,    # agents use this as cwd for Read/Grep
            )
            elapsed = time.monotonic() - t0

            logger.info(
                "[%s] Complete | turns=%d cost=$%.4f elapsed=%.1fs",
                agent_name, cli_response.num_turns, cli_response.cost_usd, elapsed,
            )

            if VERBOSE:
                logger.debug("[%s] Raw response:\n%s", agent_name, cli_response.raw_text[:1000])

            output = self._parse_output(agent_name, cli_response.raw_text)

            return AgentResult(
                agent_name=agent_name,
                success=True,
                output=output,
                raw_text=cli_response.raw_text,
                thinking=cli_response.thinking,
                session_id=cli_response.session_id,
                cost_usd=cli_response.cost_usd,
                num_turns=cli_response.num_turns,
                elapsed_sec=elapsed,
                input_tokens=cli_response.input_tokens,
                output_tokens=cli_response.output_tokens,
                cache_read_input_tokens=cli_response.cache_read_input_tokens,
                cache_creation_input_tokens=cli_response.cache_creation_input_tokens,
                duration_ms=cli_response.duration_ms,
                tool_calls=cli_response.tool_calls,
                skills_loaded=skills_loaded,
            )

        except Exception as exc:
            logger.error("[%s] Failed: %s", agent_name, exc, exc_info=True)
            return AgentResult(
                agent_name=agent_name,
                success=False,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_system_prompt(
        self,
        agent_name:    str,
        prompt_file:   str,
        extra_context: str,
        payload:       dict[str, Any] | None = None,
    ) -> tuple[str, list[str]]:
        """
        Construct the full system prompt for an agent.

        Returns (system_prompt, skills_loaded) where skills_loaded is a list of
        human-readable names of every SKILL.md file that was injected.

        Steps:
          1. Read the agent's .md file  (cached -- file content never changes)
          2. Inject matching skills     (re-evaluated each call using payload)
          3. Append any extra_context passed by the caller
        """
        # ------------------------------------------------------------------
        # Step 1 -- Load and cache the raw .md file (payload-independent)
        # ------------------------------------------------------------------
        if agent_name not in self._base_prompt_cache:
            # Resolve prompt_file:
            #   "agents/generator.md"  -> PROJECT_ROOT / "agents/generator.md"
            #   "generator.md"         -> AGENTS_DIR / "generator.md"  (legacy)
            pf = Path(prompt_file)
            if pf.parts[0] in ("agents", "skills") or "/" in prompt_file or "\\" in prompt_file:
                md_path = PROJECT_ROOT / pf
            else:
                md_path = AGENTS_DIR / pf

            if not md_path.exists():
                raise FileNotFoundError(
                    f"Agent prompt file not found: {md_path}"
                )
            base = md_path.read_text(encoding="utf-8-sig")
            self._base_prompt_cache[agent_name] = base
            logger.debug(
                "[%s] Base prompt cached (%d chars) from %s",
                agent_name, len(base), md_path,
            )

        base_prompt = self._base_prompt_cache[agent_name]

        # ------------------------------------------------------------------
        # Step 2 -- Inject matching skills (payload-dependent, fresh each call)
        # ------------------------------------------------------------------
        if self.load_skills:
            prompt, skills_loaded = inject_skills_into_prompt(
                base_prompt=base_prompt,
                skills_dir=SKILLS_DIR,
                agent_name=agent_name,
                payload=payload,
            )
        else:
            prompt = base_prompt
            skills_loaded: list[str] = []

        # ------------------------------------------------------------------
        # Step 3 -- Append optional extra context (e.g. run-specific notes)
        # ------------------------------------------------------------------
        if extra_context:
            prompt = prompt + "\n\n" + extra_context.strip()

        return prompt, skills_loaded

    def _parse_output(self, agent_name: str, raw_text: str) -> dict[str, Any]:
        """
        Extract the structured JSON dict from the agent's text response.

        Agents are instructed to return ONLY a JSON object.
        Uses the resilient extractor from tool_executor.
        """
        try:
            result = extract_json_from_text(raw_text)
            logger.debug(
                "[%s] Output parsed -- top-level keys: %s",
                agent_name, list(result.keys()),
            )
            return result
        except JsonExtractionError as exc:
            logger.warning(
                "[%s] Could not extract JSON from response: %s\n"
                "Raw text (first 600 chars):\n%s",
                agent_name, exc, raw_text[:600],
            )
            # Return the raw text under a 'raw' key so the orchestrator can
            # still log it without crashing.
            return {"raw": raw_text, "_parse_error": str(exc)}
