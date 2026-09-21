"""Ports for identity providers, credential storage and login callbacks."""

from collections.abc import Callable
from typing import Protocol

from stratos.domain.models.auth import Identity, TokenSet

# Supplies a valid access token (refreshing if needed). Injected into the API client.
AccessTokenProvider = Callable[[], str]


class IdentityProvider(Protocol):
    """OIDC / OAuth 2.0 identity provider (Entra ID, Okta, Google Workspace, ...)."""

    def authorization_url(self, redirect_uri: str, state: str, code_challenge: str) -> str: ...

    def exchange_code(self, code: str, code_verifier: str, redirect_uri: str) -> TokenSet: ...

    def refresh(self, refresh_token: str) -> TokenSet: ...

    def userinfo(self, access_token: str) -> Identity: ...


class SecretStore(Protocol):
    """Secure credential storage. Implementations must never write plain text to disk."""

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


class CallbackReceiver(Protocol):
    """Receives the browser redirect during login."""

    def start(self) -> str:
        """Begin listening; return the redirect URI."""
        ...

    def wait(self) -> dict[str, str]:
        """Block until the redirect arrives; return its query parameters."""
        ...

    def close(self) -> None: ...
