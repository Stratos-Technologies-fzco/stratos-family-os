"""Sign-in, session and token handling. Credentials live only in the injected SecretStore."""

import hmac
import json
import secrets
import webbrowser
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import SecretStr

from stratos.domain.exceptions import AuthenticationError
from stratos.domain.interfaces import CallbackReceiver, IdentityProvider, SecretStore
from stratos.domain.models.auth import Identity, SessionStatus, TokenSet
from stratos.logging import get_logger
from stratos.utils import pkce

log = get_logger("auth")


def _not_logged_in() -> AuthenticationError:
    return AuthenticationError("You are not logged in.", hint="Run `stratos login`.")


class AuthService:
    def __init__(
        self,
        idp: IdentityProvider,
        store: SecretStore,
        *,
        issuer: str,
        receiver_factory: Callable[[], CallbackReceiver],
        open_browser: Callable[[str], object] = webbrowser.open,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._idp = idp
        self._store = store
        self._issuer = issuer
        self._receiver_factory = receiver_factory
        self._open_browser = open_browser
        self._clock = clock

    # ---- storage keys --------------------------------------------------------------------
    def _key(self, name: str) -> str:
        return f"{self._issuer}|{name}"

    def _save(self, tokens: TokenSet, identity: Identity | None) -> None:
        self._store.set(self._key("access"), tokens.access_token.get_secret_value())
        if tokens.refresh_token:
            self._store.set(self._key("refresh"), tokens.refresh_token.get_secret_value())
        meta = {
            "expires_at": tokens.expires_at.isoformat() if tokens.expires_at else None,
            "identity": identity.model_dump(mode="json") if identity else self._load_identity(),
        }
        self._store.set(self._key("meta"), json.dumps(meta))

    def _load_meta(self) -> dict[str, object]:
        raw = self._store.get(self._key("meta"))
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    def _load_identity(self) -> dict[str, object] | None:
        identity = self._load_meta().get("identity")
        return identity if isinstance(identity, dict) else None

    def _load_tokens(self) -> TokenSet | None:
        access = self._store.get(self._key("access"))
        if not access:
            return None
        refresh = self._store.get(self._key("refresh"))
        raw_expiry = self._load_meta().get("expires_at")
        return TokenSet(
            access_token=SecretStr(access),
            refresh_token=SecretStr(refresh) if refresh else None,
            expires_at=datetime.fromisoformat(str(raw_expiry)) if raw_expiry else None,
        )

    def _clear(self) -> bool:
        existed = self._store.get(self._key("access")) is not None
        for name in ("access", "refresh", "meta"):
            self._store.delete(self._key(name))
        return existed

    # ---- commands ------------------------------------------------------------------------
    def login(
        self, on_url: Callable[[str], None] | None = None, *, open_browser: bool = True
    ) -> Identity:
        verifier = pkce.generate_verifier()
        state = secrets.token_urlsafe(16)
        receiver = self._receiver_factory()
        redirect_uri = receiver.start()
        try:
            url = self._idp.authorization_url(redirect_uri, state, pkce.challenge_for(verifier))
            if on_url:
                on_url(url)
            if open_browser:
                self._open_browser(url)
            params = receiver.wait()
        finally:
            receiver.close()
        if "error" in params:
            raise AuthenticationError(f"Sign-in was refused: {params.get('error')}.")
        if not hmac.compare_digest(params.get("state", ""), state):
            raise AuthenticationError("Sign-in response failed the state check; login aborted.")
        if not params.get("code"):
            raise AuthenticationError("Sign-in response contained no authorisation code.")
        tokens = self._idp.exchange_code(params["code"], verifier, redirect_uri)
        identity = self._idp.userinfo(tokens.access_token.get_secret_value())
        self._save(tokens, identity)
        log.info("login succeeded for %s", identity.display)
        return identity

    def logout(self) -> bool:
        """Remove all stored credentials. Returns False if there was no session."""
        return self._clear()

    def status(self) -> SessionStatus:
        """Local check only; never contacts the network."""
        tokens = self._load_tokens()
        if tokens is None:
            return SessionStatus(authenticated=False, issuer=self._issuer)
        expired = tokens.is_expired(self._clock())
        usable = not expired or tokens.refresh_token is not None
        identity = self._load_identity() or {}
        return SessionStatus(
            authenticated=usable,
            expired=expired,
            subject=identity.get("subject"),  # type: ignore[arg-type]
            email=identity.get("email"),  # type: ignore[arg-type]
            organisation=identity.get("organisation"),  # type: ignore[arg-type]
            expires_at=tokens.expires_at,
            issuer=self._issuer,
        )

    def whoami(self) -> Identity:
        identity = self._load_identity()
        if identity is None or not self.status().authenticated:
            raise _not_logged_in()
        return Identity.model_validate(identity)

    def access_token(self) -> str:
        """A valid access token, refreshing it first if it has expired."""
        tokens = self._load_tokens()
        if tokens is None:
            raise _not_logged_in()
        if tokens.is_expired(self._clock()):
            if tokens.refresh_token is None:
                self._clear()
                raise AuthenticationError("Your session has expired.", hint="Run `stratos login`.")
            try:
                tokens = self._idp.refresh(tokens.refresh_token.get_secret_value())
            except AuthenticationError:
                self._clear()
                raise AuthenticationError(
                    "Your session has expired.", hint="Run `stratos login`."
                ) from None
            self._save(tokens, None)
        return tokens.access_token.get_secret_value()
