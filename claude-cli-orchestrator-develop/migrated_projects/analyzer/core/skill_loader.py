"""
skill_loader.py -- Load skill context files and inject them into agent prompts.

Skills are Markdown files stored under:
    skills/<agent_name>/<category>/SKILL.md

Each SKILL.md may contain a YAML frontmatter block that declares:
  - name        : Human-readable skill name (used in logging and section headers)
  - description : What the skill does (documentation only)
  - triggers    : Payload key-value conditions -- ANY one matching loads the skill.
                  Empty or absent triggers block means the skill always loads.

Trigger matching
----------------
The `triggers` block is a flat mapping of payload_key -> expected_value.
A skill is injected when ANY key in triggers matches the runtime payload (OR logic).

Single value:
    triggers:
      destination_platform: google_secops

Any-of (list value):
    triggers:
      destination_platform:
        - google_secops
        - chronicle

Multiple keys (OR across keys):
    triggers:
      source_platform: splunk
      destination_platform: google_secops
    Loads when source_platform == splunk  OR  destination_platform == google_secops.

Matching is fully case-insensitive for both keys and values.  If a key is absent
from the payload that condition contributes nothing toward the OR match -- it is
simply skipped.

Directory scoping
-----------------
Skills in `skills/<agent_name>/` are loaded only for that specific agent.
No global fallback -- if `skills/<agent_name>/` does not exist, nothing is
injected.  This prevents irrelevant skills from leaking into unrelated agents.

  skills/
  +-- generator/
  |   +-- splunk/SKILL.md          <- triggers: {source_platform: splunk}
  |   +-- google_secops/SKILL.md   <- triggers: {destination_platform: google_secops}
  |   +-- migration/SKILL.md       <- triggers: {source_platform: splunk,
  |                                               destination_platform: google_secops}
  |                                    (loads if EITHER key matches -- OR logic)
  +-- reviewer/
      +-- validation/SKILL.md      <- no triggers (always loaded for reviewer)

Injection strategy
------------------
Matched skills are appended to the agent's system prompt as a clearly delimited
context block before the Claude CLI subprocess is invoked.  The agent sees all
matching skills in its system context -- no Read tool call required.

For large skill libraries, use the dynamic retrieval approach instead (Read tool
with product_docs/).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Sentinel strings that wrap injected skill context inside system prompts
_SKILL_BLOCK_HEADER = "\n\n---\n## Injected Skills Context\n\n"
_SKILL_BLOCK_FOOTER = "\n---\n"

# Matches a YAML frontmatter block at the very start of a file:
#   ---\n ... \n---\n
_FRONTMATTER_RE = re.compile(r"^---[ \t]*\n(.*?)\n---[ \t]*\n", re.DOTALL)


# ---------------------------------------------------------------------------
# Skill metadata
# ---------------------------------------------------------------------------

@dataclass
class SkillMetadata:
    """
    Parsed metadata from a SKILL.md frontmatter block.

    Fields map directly to frontmatter keys:
        name        -> human-readable label (used in section headers + logs)
        description -> documentation string (not evaluated programmatically)
        triggers    -> key-value conditions evaluated against the runtime payload
    """
    name:        str             = ""
    description: str             = ""
    triggers:    dict[str, Any]  = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------

def _parse_frontmatter(raw_content: str) -> tuple[SkillMetadata, str]:
    """
    Extract and parse YAML frontmatter from the beginning of a SKILL.md file.

    Returns
    -------
    (metadata, body)
      metadata : Parsed SkillMetadata.  Empty SkillMetadata if no frontmatter.
      body     : File content after the closing '---' line, stripped.
                 Full raw_content if no frontmatter block is present.
    """
    match = _FRONTMATTER_RE.match(raw_content)
    if not match:
        return SkillMetadata(), raw_content

    fm_text = match.group(1)
    body    = raw_content[match.end():].strip()

    # Try PyYAML first (available in most Python environments).
    # Fall back to the lightweight built-in parser if yaml is not installed.
    try:
        import yaml  # type: ignore
        data: dict = yaml.safe_load(fm_text) or {}
    except Exception:
        data = _simple_yaml_parse(fm_text)

    # Normalise triggers: must be a dict (not None / empty string)
    raw_triggers = data.get("triggers") or {}
    if not isinstance(raw_triggers, dict):
        raw_triggers = {}

    return SkillMetadata(
        name=str(data.get("name", "")).strip(),
        description=str(data.get("description", "")).strip(),
        triggers=raw_triggers,
    ), body


def _simple_yaml_parse(text: str) -> dict:
    """
    Minimal fallback YAML parser that handles the flat key: value and
    key: [list] structures used in SKILL.md frontmatter.

    Does NOT handle nested objects, multi-line strings, or quoted strings
    with colons.  Used only when PyYAML is unavailable.
    """
    result:       dict          = {}
    current_key:  str | None    = None
    current_list: list | None   = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Indented list item (two or more leading spaces + "- ")
        if re.match(r"^ {2,}- ", line):
            if current_key is not None:
                if current_list is None:
                    current_list = []
                    result[current_key] = current_list
                current_list.append(stripped[2:].strip().strip("\"'"))
            continue

        if ":" in stripped:
            current_list = None  # any new key ends a list
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip("\"'")
            current_key = key
            if val:
                result[key] = val
            # else: waiting for indented content (nested block or list)

    return result


# ---------------------------------------------------------------------------
# Trigger evaluator
# ---------------------------------------------------------------------------

def _eval_triggers(triggers: dict[str, Any], payload: dict[str, Any]) -> bool:
    """
    Return True if ANY condition in `triggers` is satisfied by `payload` (OR logic).

    Rules
    -----
    - Empty triggers dict -> True (unconditional -- always load this skill).
    - Payload keys are matched case-insensitively against trigger keys.
    - For each key in triggers:
        str value  -> case-insensitive equality:  payload[key] == expected
        list value -> case-insensitive membership: payload[key] in expected
    - A key absent from the payload contributes nothing; it is skipped.
    - Returns True as soon as any single condition matches (OR).
    - Returns False only when no conditions match at all.
    """
    if not triggers:
        return True

    # Normalise payload keys to lowercase once for case-insensitive key lookup.
    payload_lower: dict[str, Any] = {k.lower(): v for k, v in payload.items()}

    for key, expected in triggers.items():
        actual = payload_lower.get(key.lower())
        if actual is None:
            continue  # key absent -- skip, does not count as a match or a miss

        actual_str = str(actual).lower()

        if isinstance(expected, list):
            if actual_str in [str(e).lower() for e in expected]:
                return True
        else:
            if actual_str == str(expected).lower():
                return True

    return False  # no condition matched


# ---------------------------------------------------------------------------
# SkillLoader
# ---------------------------------------------------------------------------

class SkillLoader:
    """
    Loads SKILL.md files for a named agent, filtered by payload trigger conditions.

    Parameters
    ----------
    skills_dir : Path
        Root skills directory -- typically PROJECT_ROOT/skills/.
        Agent-specific skills live under skills/<agent_name>/.
    """

    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = Path(skills_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_for_agent(
        self,
        agent_name: str,
        payload:    dict[str, Any] | None = None,
    ) -> tuple[str, list[str]]:
        """
        Load skills scoped to `agent_name` and filtered by `payload` triggers.

        Parameters
        ----------
        agent_name : str
            Agent name -- determines which subdirectory to search:
            ``skills/<agent_name>/``
        payload : dict | None
            Runtime payload whose keys/values are matched against each skill's
            ``triggers`` block.  Pass None to skip trigger filtering (load all
            unconditional skills for the agent regardless of their triggers).

        Returns
        -------
        (context_string, skill_names_loaded)
          context_string   : Formatted context ready to append to a system prompt.
                             Empty string if no skills directory exists or no skills match.
          skill_names_loaded : List of human-readable skill names that were injected.
        """
        agent_dir = self.skills_dir / agent_name
        if not agent_dir.exists():
            logger.debug(
                "No skills directory for agent '%s' -- skipping injection (%s)",
                agent_name, agent_dir,
            )
            return "", []

        skill_files = sorted(agent_dir.rglob("SKILL.md"))
        if not skill_files:
            logger.debug("No SKILL.md files found under %s", agent_dir)
            return "", []

        sections:     list[str] = []
        names_loaded: list[str] = []
        skipped = 0

        effective_payload = payload or {}

        for skill_path in skill_files:
            section, label = self._load_one(skill_path, effective_payload)
            if section:
                sections.append(section)
                names_loaded.append(label)
            else:
                skipped += 1

        if not sections:
            logger.debug(
                "[%s] No skills injected -- %d file(s) checked, %d skipped (triggers not met)",
                agent_name, len(skill_files), skipped,
            )
            return "", []

        logger.info(
            "[%s] Injecting %d skill(s): %s  |  %d skipped (triggers not met)",
            agent_name, len(names_loaded), names_loaded, skipped,
        )
        return _SKILL_BLOCK_HEADER + "\n\n".join(sections) + _SKILL_BLOCK_FOOTER, names_loaded

    def list_skills(self) -> list[str]:
        """Return the paths of all SKILL.md files found under skills_dir."""
        if not self.skills_dir.exists():
            return []
        return [str(p) for p in sorted(self.skills_dir.rglob("SKILL.md"))]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_one(self, skill_path: Path, payload: dict[str, Any]) -> tuple[str, str]:
        """
        Load and evaluate a single SKILL.md file.

        Returns
        -------
        (section_string, skill_label)
          section_string : Formatted skill block, or "" if the skill is skipped.
          skill_label    : Human-readable name of the skill, or "" if skipped.
        """
        try:
            raw = skill_path.read_text(encoding="utf-8-sig").strip()
            if not raw:
                logger.warning("Empty skill file: %s", skill_path)
                return "", ""

            metadata, body = _parse_frontmatter(raw)

            # Evaluate trigger conditions -- skip if not met
            if not _eval_triggers(metadata.triggers, payload):
                logger.debug(
                    "Skill skipped -- triggers not met  |  file=%s  triggers=%s",
                    skill_path, metadata.triggers,
                )
                return "", ""

            if not body:
                logger.warning("Skill has frontmatter but empty body: %s", skill_path)
                return "", ""

            # Build a human-readable section title
            relative    = skill_path.relative_to(self.skills_dir)
            title_parts = relative.parts[:-1]   # drop "SKILL.md" filename
            path_title  = " / ".join(title_parts) if title_parts else skill_path.parent.name
            label       = metadata.name or path_title

            logger.debug("Loaded skill: %s", label)
            return f"### Skill: {label}\n\n{body}", label

        except (OSError, UnicodeDecodeError) as exc:
            logger.error("Failed to read skill file %s: %s", skill_path, exc)
            return "", ""


# ---------------------------------------------------------------------------
# Module-level convenience function (called by AgentRunner)
# ---------------------------------------------------------------------------

def inject_skills_into_prompt(
    base_prompt: str,
    skills_dir:  Path,
    agent_name:  str = "",
    payload:     dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """
    Append matching skill context to an agent's base system prompt.

    Parameters
    ----------
    base_prompt : str
        The agent's original system prompt (content of its .md file).
    skills_dir  : Path
        Root of the skills directory (PROJECT_ROOT/skills/).
    agent_name  : str
        Agent name for directory scoping.
        No skills are loaded if agent_name is empty.
    payload     : dict | None
        Runtime payload used to evaluate skill trigger conditions.
        Skills whose triggers do not match are silently excluded.
        Pass None to load all unconditional skills regardless of triggers.

    Returns
    -------
    (updated_prompt, skills_loaded)
      updated_prompt : base_prompt + injected skill block (or base_prompt unchanged).
      skills_loaded  : List of human-readable skill names that were injected.
    """
    if not agent_name:
        return base_prompt, []

    loader                    = SkillLoader(skills_dir)
    skill_context, skill_names = loader.load_for_agent(agent_name, payload)

    if not skill_context:
        return base_prompt, []

    return base_prompt + skill_context, skill_names
