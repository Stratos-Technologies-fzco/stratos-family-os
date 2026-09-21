"""Typed settings. Sources are merged by config/loader.py, not here."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

DEFAULT_GITHUB_ORG = "Stratos-Technologies-fzco"


class ApiSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: HttpUrl = HttpUrl("https://api.stratos.example")


class GithubSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization: str = DEFAULT_GITHUB_ORG
    api_url: str = "https://api.github.com"


class AiSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = "claude"
    model: str = "claude-sonnet-5"
    max_tokens: int = 1024
    azure_endpoint: str | None = None
    azure_api_version: str = "2024-10-21"


class DefaultsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment: str = "development"


class AuthSettings(BaseModel):
    """Public-client OIDC settings. No secrets: the login flow uses PKCE."""

    model_config = ConfigDict(extra="forbid")
    issuer: str | None = None
    client_id: str | None = None
    scopes: str = "openid profile email offline_access"


class SkillsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    registry: str | None = None  # directory containing index.json


class McpSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    registry: str | None = None  # directory containing index.json
    allowed: list[str] | None = None  # organisation allow-list; None means unrestricted


class KnowledgeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paths: list[str] = Field(default_factory=list)  # directories of Markdown documents
    pdf_paths: list[str] = Field(default_factory=list)  # directories of PDF documents
    github: list[str] = Field(default_factory=list)  # org/repo or org/repo:path
    confluence_url: str | None = None  # https://<site>.atlassian.net
    confluence_spaces: list[str] = Field(default_factory=list)
    sharepoint_sites: list[str] = Field(default_factory=list)  # host:/sites/name


class AgentsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_permissions: list[str] = Field(default_factory=lambda: ["knowledge_read", "repo_read"])
    max_steps: int = 6


class PolicySettings(BaseModel):
    """Organisation policy. Normally set in the organisation-level configuration file."""

    model_config = ConfigDict(extra="forbid")
    allow_public_repos: bool = True
    allowed_ai_providers: list[str] | None = None  # None means any supported provider
    allowed_ai_models: list[str] | None = None  # None means any model


class MonitoringSettings(BaseModel):
    """Thresholds used by `stratos audit summary` to raise alerts."""

    model_config = ConfigDict(extra="forbid")
    window_days: int = 7
    denied_threshold: int = 5
    failure_rate_threshold: float = 0.25
    min_events_for_rate: int = 5


class ProjectSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: str = "none"  # default CI template: python | node | none
    default_owners: list[str] = Field(default_factory=list)  # CODEOWNERS entries
    instructions_file: str | None = None  # organisation instructions for CLAUDE.md


class WorkspaceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str | None = None  # default: ~/stratos-workspaces


class Settings(BaseSettings):
    """Resolved configuration. Environment variables use the STRATOS_ prefix
    and `__` for nesting, e.g. STRATOS_API__ENDPOINT."""

    model_config = SettingsConfigDict(
        env_prefix="STRATOS_", env_nested_delimiter="__", extra="forbid", frozen=True
    )

    organisation: str = DEFAULT_GITHUB_ORG
    api: ApiSettings = Field(default_factory=ApiSettings)
    github: GithubSettings = Field(default_factory=GithubSettings)
    ai: AiSettings = Field(default_factory=AiSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    defaults: DefaultsSettings = Field(default_factory=DefaultsSettings)
    skills: SkillsSettings = Field(default_factory=SkillsSettings)
    mcp: McpSettings = Field(default_factory=McpSettings)
    knowledge: KnowledgeSettings = Field(default_factory=KnowledgeSettings)
    agents: AgentsSettings = Field(default_factory=AgentsSettings)
    policy: PolicySettings = Field(default_factory=PolicySettings)
    monitoring: MonitoringSettings = Field(default_factory=MonitoringSettings)
    project: ProjectSettings = Field(default_factory=ProjectSettings)
    workspace: WorkspaceSettings = Field(default_factory=WorkspaceSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Layering is done explicitly in the loader; only init values are used here.
        return (init_settings,)

    def to_flat(self) -> dict[str, Any]:
        """Dotted-key view, e.g. {"api.endpoint": "..."}."""
        return flatten(self.model_dump(mode="json"))


def flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flatten(value, f"{dotted}."))
        else:
            out[dotted] = value
    return out
