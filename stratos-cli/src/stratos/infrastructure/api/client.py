"""The Stratos SDK: the single HTTP client for all Platform API traffic.

Retry policy (bounded, exponential backoff, Retry-After honoured):
  * 429 is retried for every method: the server refused the request without processing it.
  * 502/503/504 and network failures are retried only for safe methods (GET/HEAD/OPTIONS),
    or when the caller supplies an idempotency key. Destructive calls are never retried blindly.
"""

import random
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from stratos import __version__
from stratos.domain.exceptions import (
    APIError,
    AuthenticationError,
    AuthorizationError,
    NetworkError,
    ResourceNotFoundError,
    StratosError,
    ValidationError,
)
from stratos.domain.interfaces import AccessTokenProvider
from stratos.logging import get_correlation_ids, get_logger, new_id
from stratos.utils.redaction import SecretRedactor
from stratos.utils.validation import require_secure_url, sanitize_text

log = get_logger("api")
_redactor = SecretRedactor()
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
RETRYABLE_STATUS = frozenset({502, 503, 504})
MAX_RETRY_AFTER = 60.0


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        return max(0.0, (parsedate_to_datetime(raw) - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None


class PlatformApiClient:
    def __init__(
        self,
        base_url: str,
        token_provider: AccessTokenProvider | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        max_backoff: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._base_url = require_secure_url(base_url.rstrip("/"), "api.endpoint")
        self._token_provider = token_provider
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._max_backoff = max_backoff
        self._sleep = sleep
        self._jitter = jitter
        self._http = httpx.Client(
            base_url=self._base_url, timeout=timeout, transport=transport, follow_redirects=False
        )

    # ---- lifecycle -----------------------------------------------------
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "PlatformApiClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- public API ----------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        idempotency_key: str | None = None,
        authenticated: bool = True,
    ) -> Any:
        response = self._send(
            method, path, params=params, json=json,
            idempotency_key=idempotency_key, authenticated=authenticated,
        )  # fmt: skip
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise APIError("The Platform API returned a response that is not valid JSON.") from exc

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)

    def paginate(
        self, path: str, params: dict[str, Any] | None = None, *, limit: int | None = None
    ) -> Iterator[Any]:
        """Yield items from list endpoints, following RFC 8288 `Link: <...>; rel="next"`.

        Assumes each page body is a JSON array (pending the published API contract).
        """
        yielded = 0
        target: str | None = path
        query = params
        while target:
            response = self._send("GET", target, params=query)
            page = response.json() if response.content else []
            if not isinstance(page, list):
                raise APIError("Unexpected pagination response: expected a JSON array.")
            for item in page:
                if limit is not None and yielded >= limit:
                    return
                yielded += 1
                yield item
            next_url = response.links.get("next", {}).get("url")
            target = self._same_host(next_url) if next_url else None
            query = None  # the next link already carries its query string

    # ---- internals -----------------------------------------------------
    def _same_host(self, url: str) -> str:
        """Refuse to follow pagination links to another host (would leak the bearer token)."""
        resolved = self._http.base_url.join(url)
        if (
            resolved.host != self._http.base_url.host
            or resolved.scheme != self._http.base_url.scheme
        ):
            raise APIError("Refusing to follow a pagination link to a different host.")
        return str(resolved)

    def _headers(
        self, request_id: str, authenticated: bool, idempotency_key: str | None
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": f"stratos-cli/{__version__}",
            "X-Request-ID": request_id,
            "X-Correlation-ID": get_correlation_ids()[1],
        }
        if authenticated and self._token_provider is not None:
            headers["Authorization"] = f"Bearer {self._token_provider()}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return retry_after
        delay = self._backoff_base * float(2**attempt)
        jitter: float = self._jitter()
        return min(delay + jitter * self._backoff_base, self._max_backoff)

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        idempotency_key: str | None = None,
        authenticated: bool = True,
    ) -> httpx.Response:
        method = method.upper()
        may_retry_failures = method in SAFE_METHODS or idempotency_key is not None
        attempt = 0
        while True:
            request_id = new_id()
            headers = self._headers(request_id, authenticated, idempotency_key)
            try:
                response = self._http.request(
                    method, path, params=params, json=json, headers=headers
                )
            except httpx.HTTPError as exc:
                if may_retry_failures and attempt < self._max_retries:
                    log.debug(
                        "%s %s network error (%s), retry %d",
                        method,
                        path,
                        type(exc).__name__,
                        attempt + 1,
                    )
                    self._sleep(self._backoff(attempt, None))
                    attempt += 1
                    continue
                raise NetworkError(
                    f"Cannot reach the Platform API ({type(exc).__name__}).",
                    hint="Check your network connection and `stratos config get api.endpoint`.",
                ) from exc

            log.debug("%s %s -> %d (request %s)", method, path, response.status_code, request_id)
            if response.is_success:
                return response

            retryable = response.status_code == 429 or (
                response.status_code in RETRYABLE_STATUS and may_retry_failures
            )
            if retryable and attempt < self._max_retries:
                wait = _retry_after(response)
                if wait is None or wait <= MAX_RETRY_AFTER:
                    self._sleep(self._backoff(attempt, wait))
                    attempt += 1
                    continue
            raise self._error_for(response, request_id)

    @staticmethod
    def _error_for(response: httpx.Response, request_id: str) -> StratosError:
        status = response.status_code
        detail = f"HTTP {status}"
        try:
            body = response.json()
            if isinstance(body, dict):
                for key in ("message", "detail", "error"):
                    if isinstance(body.get(key), str):
                        detail = body[key]
                        break
        except ValueError:
            pass
        message = f"{sanitize_text(_redactor.redact_text(detail))[:300]} (request {request_id})"
        if status == 401:
            return AuthenticationError(message, hint="Run `stratos login`.")
        if status == 403:
            return AuthorizationError(message)
        if status == 404:
            return ResourceNotFoundError(message)
        if status in (400, 409, 422):
            return ValidationError(message)
        return APIError(message)
