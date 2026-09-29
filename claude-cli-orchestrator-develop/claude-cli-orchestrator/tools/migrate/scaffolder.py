"""
scaffolder.py -- Step 2 of the migration pipeline.

Deterministic file operations:
  1. Validate the AgentWeave source project
  2. Create the destination directory (or clear it if --force)
  3. Copy the orchestrator engine bundle into the destination
  4. Copy project-specific assets:
       - agents/*.md         (skip supervisor.md)
       - mcp/mcp.json        (or mcp.json at project root)
       - product_docs/
       - skills/             (if present -- AgentWeave layouts vary)
  5. Scaffold empty runtime dirs: runs/, logs/, samples/

No LLM calls here. No workflow extraction. Pure file plumbing.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ScaffoldReport:
    """Summary of what the scaffolder did, consumed later by reporter.py."""
    source:            Path
    dest:              Path
    engine_root:       Path
    agents_copied:     list[str]         = field(default_factory=list)
    supervisor_path:   Path | None       = None            # copy of source supervisor.md (for extractor)
    mcp_json_path:     Path | None       = None            # absolute path in DEST
    product_docs:      bool              = False
    skills_copied:     bool              = False
    subagent_defs:     list[dict]        = field(default_factory=list)   # from source project.yaml
    source_yaml_path:  Path | None       = None            # copy of source project.yaml (for extractor)
    project_name:      str               = ""
    warnings:          list[str]         = field(default_factory=list)


class ScaffoldError(RuntimeError):
    """Raised when source validation fails fatally."""


# ---------------------------------------------------------------------------
# Engine bundle -- files/dirs copied from the orchestrator into every dest
# ---------------------------------------------------------------------------

# Paths relative to the orchestrator root (the repo that contains this file).
ENGINE_COPY_FILES = [
    "main.py",
    "test_claude_cli.py",
    "MIGRATION_GUIDE.md",
]

ENGINE_COPY_DIRS = [
    "core",
    "config",
    "observability",
]

# Created empty in the destination; the engine populates them at runtime.
RUNTIME_DIRS = [
    "runs",
    "logs",
    "samples",
]

# Files inside engine dirs that we never want to carry over.
EXCLUDED_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache"}


# ---------------------------------------------------------------------------
# Scaffolder
# ---------------------------------------------------------------------------

class Scaffolder:
    """
    Performs deterministic source validation + file copy + directory scaffolding.
    No LLM, no YAML emission. That happens later in the pipeline.
    """

    def __init__(
        self,
        source: Path,
        dest: Path,
        engine_root: Path,
        force: bool = False,
    ):
        self.source      = Path(source).resolve()
        self.dest        = Path(dest).resolve()
        self.engine_root = Path(engine_root).resolve()
        self.force       = force
        self.report      = ScaffoldReport(
            source=self.source,
            dest=self.dest,
            engine_root=self.engine_root,
        )

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> ScaffoldReport:
        self._validate_source()
        self._prepare_dest()
        self._copy_engine()
        self._copy_agents()
        self._copy_mcp()
        self._copy_product_docs()
        self._copy_skills()
        self._scaffold_runtime_dirs()
        self._stash_source_references()
        return self.report

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_source(self) -> None:
        if not self.source.is_dir():
            raise ScaffoldError(f"Source is not a directory: {self.source}")

        src_yaml = self.source / "project.yaml"
        if not src_yaml.is_file():
            raise ScaffoldError(f"Missing project.yaml in source: {src_yaml}")

        # supervisor.md can live at agents/supervisor.md OR at source root
        supervisor = self._find_supervisor()
        if supervisor is None:
            raise ScaffoldError(
                f"Missing supervisor.md in source project. Looked in:\n"
                f"  {self.source / 'agents' / 'supervisor.md'}\n"
                f"  {self.source / 'supervisor.md'}"
            )
        self.report.supervisor_path = supervisor

        agents_dir = self.source / "agents"
        if not agents_dir.is_dir():
            raise ScaffoldError(f"Missing agents/ directory in source: {agents_dir}")

        non_supervisor = [
            p for p in agents_dir.glob("*.md")
            if p.name.lower() != "supervisor.md"
        ]
        if not non_supervisor:
            raise ScaffoldError(
                f"Source project has no subagent .md files besides supervisor.md in {agents_dir}"
            )

    def _find_supervisor(self) -> Path | None:
        for candidate in [
            self.source / "agents" / "supervisor.md",
            self.source / "supervisor.md",
        ]:
            if candidate.is_file():
                return candidate
        return None

    # ------------------------------------------------------------------
    # Destination prep
    # ------------------------------------------------------------------

    def _prepare_dest(self) -> None:
        if self.dest.exists():
            if not self.force:
                raise ScaffoldError(
                    f"Destination already exists: {self.dest}\n"
                    f"Use --force to overwrite."
                )
            shutil.rmtree(self.dest)
        self.dest.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Engine bundle
    # ------------------------------------------------------------------

    def _copy_engine(self) -> None:
        for name in ENGINE_COPY_FILES:
            src = self.engine_root / name
            if src.is_file():
                shutil.copy2(src, self.dest / name)
            else:
                self.report.warnings.append(f"Engine file not found, skipped: {name}")

        for name in ENGINE_COPY_DIRS:
            src = self.engine_root / name
            if not src.is_dir():
                self.report.warnings.append(f"Engine dir not found, skipped: {name}")
                continue
            dst = self.dest / name
            shutil.copytree(src, dst, ignore=_ignore_excluded)

    # ------------------------------------------------------------------
    # Agent prompts (skip supervisor.md)
    # ------------------------------------------------------------------

    def _copy_agents(self) -> None:
        src_agents = self.source / "agents"
        dst_agents = self.dest / "agents"
        dst_agents.mkdir(parents=True, exist_ok=True)

        for md in sorted(src_agents.glob("*.md")):
            if md.name.lower() == "supervisor.md":
                continue
            shutil.copy2(md, dst_agents / md.name)
            self.report.agents_copied.append(md.name)

    # ------------------------------------------------------------------
    # MCP config
    # ------------------------------------------------------------------

    def _copy_mcp(self) -> None:
        """
        AgentWeave places mcp.json either at the project root or inside mcp/.
        The orchestrator expects it at mcp/mcp.json.
        """
        candidates = [
            self.source / "mcp" / "mcp.json",
            self.source / "mcp.json",
        ]
        src_mcp = next((c for c in candidates if c.is_file()), None)
        if src_mcp is None:
            self.report.warnings.append("No mcp.json found in source; skipping MCP config copy.")
            return

        dst_dir = self.dest / "mcp"
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst_mcp = dst_dir / "mcp.json"
        shutil.copy2(src_mcp, dst_mcp)
        self.report.mcp_json_path = dst_mcp

    # ------------------------------------------------------------------
    # product_docs
    # ------------------------------------------------------------------

    def _copy_product_docs(self) -> None:
        src = self.source / "product_docs"
        if not src.is_dir():
            return
        shutil.copytree(src, self.dest / "product_docs", ignore=_ignore_excluded)
        self.report.product_docs = True

    # ------------------------------------------------------------------
    # Skills (AgentWeave layouts vary: skills/, .claude/skills/, product_docs/skills/)
    # ------------------------------------------------------------------

    def _copy_skills(self) -> None:
        """
        AgentWeave skill layouts differ across projects. The orchestrator expects
        skills/<agent_name>/**/SKILL.md. We copy whatever we find into skills/
        and flag a warning so the developer can restructure if needed.
        """
        candidates = [
            self.source / "skills",
            self.source / ".claude" / "skills",
            self.source / "product_docs" / "skills",
        ]
        src_skills = next((c for c in candidates if c.is_dir()), None)
        if src_skills is None:
            # Orchestrator is happy without skills/; do nothing.
            return

        dst = self.dest / "skills"
        shutil.copytree(src_skills, dst, ignore=_ignore_excluded)
        self.report.skills_copied = True

        # AgentWeave skills are typically not scoped per-agent. If we don't see
        # the orchestrator-expected subdirs (skills/<agent>/), warn the developer.
        agent_names = {a.stem for a in (self.dest / "agents").glob("*.md")}
        top_level = {p.name for p in dst.iterdir() if p.is_dir()}
        if not (top_level & agent_names):
            self.report.warnings.append(
                "Copied skills/ from source but no subdirectory matches an agent "
                "name. The orchestrator expects skills/<agent_name>/SKILL.md. "
                "Manual restructure likely needed."
            )

    # ------------------------------------------------------------------
    # Runtime dirs
    # ------------------------------------------------------------------

    def _scaffold_runtime_dirs(self) -> None:
        for name in RUNTIME_DIRS:
            (self.dest / name).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Stash the source supervisor.md + project.yaml in dest for the extractor
    # ------------------------------------------------------------------

    def _stash_source_references(self) -> None:
        """
        The extractor step needs the original supervisor.md and project.yaml.
        Copy them into dest/.migration_source/ so the pipeline stages after
        scaffolding don't need to re-reference the original source path.
        """
        stash = self.dest / ".migration_source"
        stash.mkdir(parents=True, exist_ok=True)

        src_yaml = self.source / "project.yaml"
        shutil.copy2(src_yaml, stash / "project.yaml")
        self.report.source_yaml_path = stash / "project.yaml"

        if self.report.supervisor_path is not None:
            shutil.copy2(self.report.supervisor_path, stash / "supervisor.md")
            self.report.supervisor_path = stash / "supervisor.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ignore_excluded(_dir: str, names: list[str]) -> list[str]:
    """shutil.copytree ignore= callback that filters out excluded dirs/files."""
    return [n for n in names if n in EXCLUDED_NAMES]
