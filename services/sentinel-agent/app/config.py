"""Configuration. Everything comes from the environment (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── LLM ──────────────────────────────────────────────────────────────
    # One model setting per flow, sized to that flow's task complexity —
    # deliberately not a single shared `llm_model`, so a flow can be tuned
    # (or swapped to a cheaper/stronger model) without affecting the others.
    llm_model_parser: str = "claude-opus-5"              # parser generation: reasoning-heavy
    llm_model_workbook_manifest: str = "claude-opus-5"   # panel planning: reasoning-heavy
    llm_model_panel: str = "claude-sonnet-5"             # per-panel loop: high-volume, cheaper

    # "anthropic": Anthropic API, billed against anthropic_api_key.
    # "claude_cli": shells out to a local `claude` binary using a Claude Code
    # subscription (no API key) — see app/llm/claude_cli.py for the
    # trade-offs (no in-generation run_kql/run_python self-check).
    llm_provider: str = "anthropic"

    anthropic_api_key: str = ""

    # ── Claude CLI (only used when llm_provider == "claude_cli") ─────────
    claude_cli_bin: str = ""          # empty = resolve via PATH at first use
    claude_cli_timeout: int = 600
    claude_code_oauth_token: str = ""  # from `claude setup-token`

    # Model used by /v1/chat/completions — the OpenAI-compatible proxy that
    # lets n8n's own orchestrator chat model run through the claude CLI too
    # (see app/llm/openai_compat.py). The incoming request's `model` field is
    # whatever n8n's OpenAI Chat Model node happens to be configured with —
    # a meaningless placeholder from that node's perspective — so it's
    # ignored in favor of this setting. Independent of claude_cli_* above:
    # this endpoint always uses the CLI, regardless of llm_provider.
    orchestrator_model: str = "claude-opus-5"

    # ── Azure: management plane ──────────────────────────────────────────
    azure_tenant_id: str = ""
    azure_subscription_id: str = ""
    azure_resource_group: str = ""
    azure_location: str = "eastus"
    azure_client_id: str = ""
    azure_client_secret: str = ""

    # ── Log Analytics ────────────────────────────────────────────────────
    azure_workspace_name: str = ""
    # Workspace *customer id* (the GUID labelled "Workspace ID" in the portal),
    # not the ARM resource id. The data-plane query API keys off this.
    azure_workspace_id: str = ""

    # ── Azure: data plane (read-only SP; falls back to the mgmt one) ─────
    azure_logs_client_id: str = ""
    azure_logs_client_secret: str = ""

    # ── Behaviour ────────────────────────────────────────────────────────
    validate_panel_queries: bool = False
    panel_concurrency: int = 4
    panel_max_retries: int = 3
    python_exec_timeout: int = 30

    database_url: str = "postgresql://sentinel:sentinel@postgres:5432/sentinel"
    prompts_dir: Path = Path("/prompts")
    reference_dir: Path = Path("/reference")

    log_level: str = "INFO"

    # ── Derived ──────────────────────────────────────────────────────────
    @property
    def logs_client_id(self) -> str:
        return self.azure_logs_client_id or self.azure_client_id

    @property
    def logs_client_secret(self) -> str:
        return self.azure_logs_client_secret or self.azure_client_secret

    @property
    def workspace_resource_id(self) -> str:
        """ARM id of the workspace — used as a workbook's sourceId."""
        return (
            f"/subscriptions/{self.azure_subscription_id}"
            f"/resourceGroups/{self.azure_resource_group}"
            f"/providers/Microsoft.OperationalInsights"
            f"/workspaces/{self.azure_workspace_name}"
        )

    def azure_configured(self) -> bool:
        """False when Azure creds are absent — KQL validation degrades to a
        skip rather than a hard failure, so the sidecar is usable offline."""
        return bool(
            self.azure_tenant_id and self.logs_client_id and self.azure_workspace_id
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
