from datetime import UTC, datetime, timedelta

from pydantic import SecretStr

from stratos.domain.models import StratosModel


class Identity(StratosModel):
    """Who the signed-in user is. Contains no credentials."""

    subject: str
    email: str | None = None
    name: str | None = None
    organisation: str | None = None
    roles: tuple[str, ...] = ()

    @property
    def display(self) -> str:
        return self.email or self.name or self.subject


class TokenSet(StratosModel):
    """Credentials from the identity provider. Secret fields never print in full."""

    access_token: SecretStr
    refresh_token: SecretStr | None = None
    expires_at: datetime | None = None

    def is_expired(self, now: datetime | None = None, skew_seconds: int = 30) -> bool:
        if self.expires_at is None:
            return False
        return (now or datetime.now(UTC)) + timedelta(seconds=skew_seconds) >= self.expires_at


class SessionStatus(StratosModel):
    authenticated: bool
    expired: bool = False
    subject: str | None = None
    email: str | None = None
    organisation: str | None = None
    expires_at: datetime | None = None
    issuer: str | None = None
