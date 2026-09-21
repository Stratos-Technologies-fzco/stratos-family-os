"""AI provider factory. Vendor modules load only when their provider is requested."""

from stratos.domain.exceptions import ConfigurationError
from stratos.domain.interfaces import AIProvider

_ANTHROPIC = {"claude", "anthropic"}
_OPENAI = {"openai"}
_AZURE = {"azure-openai", "azure_openai", "azure"}
SUPPORTED = "claude, openai, azure-openai"


def api_key_env_for(provider: str) -> str:
    """Environment variable that holds the API key for a provider."""
    name = provider.lower()
    if name in _OPENAI:
        return "OPENAI_API_KEY"
    if name in _AZURE:
        return "AZURE_OPENAI_API_KEY"
    return "ANTHROPIC_API_KEY"


def create_provider(
    name: str,
    *,
    api_key: str | None,
    default_model: str,
    azure_endpoint: str | None = None,
    azure_api_version: str = "2024-10-21",
) -> AIProvider:
    lowered = name.lower()
    if lowered in _ANTHROPIC:
        from stratos.infrastructure.ai.anthropic import AnthropicProvider

        return AnthropicProvider(api_key, default_model=default_model)
    if lowered in _OPENAI:
        from stratos.infrastructure.ai.openai_compat import OpenAIProvider

        return OpenAIProvider(api_key, default_model=default_model)
    if lowered in _AZURE:
        from stratos.infrastructure.ai.openai_compat import AzureOpenAIProvider

        return AzureOpenAIProvider(
            api_key,
            endpoint=azure_endpoint,
            api_version=azure_api_version,
            default_model=default_model,
        )
    raise ConfigurationError(
        f"Unknown AI provider '{name}'.",
        hint=f"Supported providers: {SUPPORTED}. Change it with `stratos config set ai.provider`.",
    )
