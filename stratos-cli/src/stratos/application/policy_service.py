"""Organisation policy enforcement (settings-driven; set in the organisation config layer)."""

from typing import Any

from stratos.config.settings import Settings
from stratos.domain.exceptions import AuthorizationError


class PolicyService:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def allow_public_repos(self) -> bool:
        return self._s.policy.allow_public_repos

    def check_public_repo(self) -> None:
        if not self._s.policy.allow_public_repos:
            raise AuthorizationError(
                "Public repositories are not permitted by organisation policy.",
                hint="Create a private repository, or ask an administrator to change "
                "policy.allow_public_repos.",
            )

    def check_provider(self, provider: str) -> None:
        allowed = self._s.policy.allowed_ai_providers
        if allowed is not None and provider.lower() not in {p.lower() for p in allowed}:
            raise AuthorizationError(
                f"AI provider '{provider}' is not permitted by organisation policy.",
                hint=f"Permitted providers: {', '.join(allowed) or 'none'}.",
            )

    def check_model(self, model: str | None) -> None:
        allowed = self._s.policy.allowed_ai_models
        if model and allowed is not None and model not in allowed:
            raise AuthorizationError(
                f"AI model '{model}' is not permitted by organisation policy.",
                hint=f"Permitted models: {', '.join(allowed) or 'none'}.",
            )

    def summary(self) -> dict[str, Any]:
        p, s = self._s.policy, self._s
        return {
            "stratos.policy.allow_public_repos": p.allow_public_repos,
            "stratos.policy.allowed_ai_providers": p.allowed_ai_providers or "any",
            "stratos.policy.allowed_ai_models": p.allowed_ai_models or "any",
            "stratos.mcp.allowed": s.mcp.allowed if s.mcp.allowed is not None else "unrestricted",
            "stratos.agents.allowed_permissions": ", ".join(s.agents.allowed_permissions),
        }
