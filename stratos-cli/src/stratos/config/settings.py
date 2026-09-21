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


class AiSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = "claude"


class DefaultsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment: str = "development"


class AuthSettings(BaseModel):
    """Public-client OIDC settings. No secrets: the login flow uses PKCE."""

    model_config = ConfigDict(extra="forbid")
    issuer: str | None = None
    client_id: str | None = None
    scopes: str = "openid profile email offline_access"


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
