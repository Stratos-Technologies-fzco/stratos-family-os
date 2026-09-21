"""Generic OpenID Connect provider (authorization code + PKCE, public client).

Works with any compliant issuer (Entra ID, Okta, Google Workspace, ...) via discovery.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import SecretStr

from stratos.domain.exceptions import AuthenticationError, ConfigurationError, NetworkError
from stratos.domain.models.auth import Identity, TokenSet
from stratos.utils.redaction import SecretRedactor
from stratos.utils.validation import require_secure_url

_redactor = SecretRedactor()


class OidcProvider:
    def __init__(
        self,
        issuer: str,
        client_id: str,
        scopes: str = "openid profile email offline_access",
        *,
        http: httpx.Client | None = None,
        clock: Any = lambda: datetime.now(UTC),
    ) -> None:
        self.issuer = require_secure_url(issuer.rstrip("/"), "auth.issuer")
        self._client_id = client_id
        self._scopes = scopes
        self._http = http or httpx.Client(timeout=10.0)
        self._clock = clock
        self._discovery: dict[str, Any] | None = None

    # ---- discovery -----------------------------------------------------
    def _get_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._http.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise NetworkError(f"Cannot reach the identity provider: {type(exc).__name__}") from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.is_success and isinstance(body, dict):
            return body
        detail = (
            body.get("error_description") or body.get("error") if isinstance(body, dict) else None
        )
        message = _redactor.redact_text(str(detail or f"HTTP {response.status_code}"))[:200]
        raise AuthenticationError(f"Identity provider rejected the request: {message}")

    def _endpoints(self) -> dict[str, Any]:
        if self._discovery is None:
            doc = self._get_json("GET", f"{self.issuer}/.well-known/openid-configuration")
            for key in ("authorization_endpoint", "token_endpoint"):
                if key not in doc:
                    raise ConfigurationError(f"Issuer discovery document is missing '{key}'.")
                require_secure_url(doc[key], key)
            self._discovery = doc
        return self._discovery

    # ---- IdentityProvider ---------------------------------------------
    def authorization_url(self, redirect_uri: str, state: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": redirect_uri,
                "scope": self._scopes,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self._endpoints()['authorization_endpoint']}?{query}"

    def exchange_code(self, code: str, code_verifier: str, redirect_uri: str) -> TokenSet:
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
                "client_id": self._client_id,
            }
        )

    def refresh(self, refresh_token: str) -> TokenSet:
        tokens = self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._client_id,
            }
        )
        if tokens.refresh_token is None:  # providers may omit it; keep the existing one
            tokens = tokens.model_copy(update={"refresh_token": SecretStr(refresh_token)})
        return tokens

    def userinfo(self, access_token: str) -> Identity:
        endpoint = self._endpoints().get("userinfo_endpoint")
        if not endpoint:
            raise ConfigurationError("Issuer does not advertise a userinfo endpoint.")
        claims = self._get_json(
            "GET", endpoint, headers={"Authorization": f"Bearer {access_token}"}
        )
        roles = claims.get("roles") or claims.get("groups") or ()
        return Identity(
            subject=str(claims["sub"]),
            email=claims.get("email"),
            name=claims.get("name"),
            organisation=claims.get("organisation") or claims.get("org"),
            roles=tuple(str(r) for r in roles) if isinstance(roles, list | tuple) else (),
        )

    def _token_request(self, form: dict[str, str]) -> TokenSet:
        body = self._get_json("POST", self._endpoints()["token_endpoint"], data=form)
        access = body.get("access_token")
        if not access:
            raise AuthenticationError("Identity provider did not return an access token.")
        expires_in = body.get("expires_in")
        return TokenSet(
            access_token=SecretStr(access),
            refresh_token=SecretStr(body["refresh_token"]) if body.get("refresh_token") else None,
            expires_at=self._clock() + timedelta(seconds=int(expires_in)) if expires_in else None,
        )
