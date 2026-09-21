"""Per-invocation command context (dependency injection).

Everything expensive is built lazily, so `--help` never touches configuration,
the keyring, the network or any client.
"""

from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any

from stratos.cli.output import ConsoleRenderer
from stratos.config.loader import load_settings
from stratos.config.settings import Settings
from stratos.domain.enums import Permission
from stratos.domain.exceptions import AuthenticationError, ConfigurationError
from stratos.domain.models.auth import Identity
from stratos.utils.redaction import SecretRedactor

if TYPE_CHECKING:
    from stratos.application.audit_service import AuditService
    from stratos.application.auth_service import AuthService
    from stratos.application.authorization import AuthorizationService
    from stratos.infrastructure.api.client import PlatformApiClient


@dataclass
class CliContext:
    renderer: ConsoleRenderer
    redactor: SecretRedactor = field(default_factory=SecretRedactor)
    verbose: bool = False
    debug: bool = False
    overrides: dict[str, Any] = field(default_factory=dict)

    @cached_property
    def settings(self) -> Settings:
        return load_settings(self.overrides)

    @cached_property
    def auth_service(self) -> "AuthService":
        from stratos.application.auth_service import AuthService
        from stratos.infrastructure.auth.loopback import LoopbackReceiver
        from stratos.infrastructure.auth.oidc import OidcProvider
        from stratos.infrastructure.secrets import KeyringSecretStore

        auth = self.settings.auth
        if not auth.issuer or not auth.client_id:
            raise ConfigurationError(
                "Sign-in is not configured.",
                hint=(
                    "Run `stratos config set auth.issuer <url>` and "
                    "`stratos config set auth.client_id <id>`."
                ),
            )
        return AuthService(
            OidcProvider(auth.issuer, auth.client_id, auth.scopes),
            KeyringSecretStore(),
            issuer=auth.issuer,
            receiver_factory=LoopbackReceiver,
        )

    @cached_property
    def authorization(self) -> "AuthorizationService":
        from stratos.application.authorization import AuthorizationService

        return AuthorizationService()

    @cached_property
    def api(self) -> "PlatformApiClient":
        from stratos.infrastructure.api.client import PlatformApiClient

        return PlatformApiClient(str(self.settings.api.endpoint), self.auth_service.access_token)

    @cached_property
    def audit(self) -> "AuditService":
        from stratos.application.audit_service import AuditService
        from stratos.infrastructure.filesystem.audit_log import LocalAuditStore

        return AuditService(LocalAuditStore(redactor=self.redactor), self._identity_or_none)

    def _identity_or_none(self) -> Identity | None:
        try:
            return self.auth_service.whoami()
        except (AuthenticationError, ConfigurationError):
            return None

    def require(self, permission: Permission) -> Identity:
        """Return the signed-in identity after checking it holds `permission`."""
        identity = self.auth_service.whoami()
        self.authorization.require(identity, permission)
        return identity
