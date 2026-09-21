"""Async variant of the Platform API client for network-bound work (parallel fetches, remote
knowledge sources). Same policy as the sync client: TLS only, request IDs, bounded retries with
backoff and Retry-After, no blind retries of destructive calls, same-host pagination only."""

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any

import httpx

from stratos.domain.exceptions import APIError, NetworkError
from stratos.domain.interfaces import AccessTokenProvider
from stratos.infrastructure.api.client import (
    MAX_RETRY_AFTER,
    RETRYABLE_STATUS,
    SAFE_METHODS,
    _retry_after,
    build_headers,
    compute_backoff,
    error_for,
    is_rate_limited,
)
from stratos.logging import get_logger, new_id
from stratos.utils.validation import require_secure_url

log = get_logger("api.async")


class AsyncPlatformApiClient:
    def __init__(
        self,
        base_url: str,
        token_provider: AccessTokenProvider | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        max_backoff: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
        default_headers: Mapping[str, str] | None = None,
        send_correlation: bool = True,
        auth_scheme: str = "Bearer",
    ) -> None:
        self._base_url = require_secure_url(base_url.rstrip("/"), "api endpoint")
        self._token_provider = token_provider
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._max_backoff = max_backoff
        self._sleep = sleep
        self._jitter = jitter
        self._default_headers = dict(default_headers or {})
        self._send_correlation = send_correlation
        self._auth_scheme = auth_scheme
        self._http = httpx.AsyncClient(
            base_url=self._base_url, timeout=timeout, transport=transport, follow_redirects=False
        )

    # ---- lifecycle -----------------------------------------------------------------------
    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncPlatformApiClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ---- public API ----------------------------------------------------------------------
    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        idempotency_key: str | None = None,
        authenticated: bool = True,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        response = await self.send(
            method, path, params=params, json=json, idempotency_key=idempotency_key,
            authenticated=authenticated, headers=headers,
        )  # fmt: skip
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise APIError("The API returned a response that is not valid JSON.") from exc

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self.request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> Any:
        return await self.request("PUT", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> Any:
        return await self.request("DELETE", path, **kwargs)

    async def get_text(self, path: str, **kwargs: Any) -> str:
        return (await self.send("GET", path, **kwargs)).text

    async def paginate(
        self, path: str, params: dict[str, Any] | None = None, *, limit: int | None = None
    ) -> AsyncIterator[Any]:
        """Yield items following RFC 8288 `Link: rel="next"`; never leaves the API's host."""
        yielded = 0
        target: str | None = path
        query = params
        while target:
            response = await self.send("GET", target, params=query)
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
            query = None

    # ---- internals -----------------------------------------------------------------------
    def _same_host(self, url: str) -> str:
        resolved = self._http.base_url.join(url)
        if (
            resolved.host != self._http.base_url.host
            or resolved.scheme != self._http.base_url.scheme
        ):
            raise APIError("Refusing to follow a pagination link to a different host.")
        return str(resolved)

    async def send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        idempotency_key: str | None = None,
        authenticated: bool = True,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        method = method.upper()
        may_retry_failures = method in SAFE_METHODS or idempotency_key is not None
        attempt = 0
        while True:
            request_id = new_id()
            merged = build_headers(
                request_id=request_id,
                authenticated=authenticated,
                idempotency_key=idempotency_key,
                token_provider=self._token_provider,
                auth_scheme=self._auth_scheme,
                default_headers={**self._default_headers, **(headers or {})},
                send_correlation=self._send_correlation,
            )
            try:
                response = await self._http.request(
                    method, path, params=params, json=json, headers=merged
                )
            except httpx.HTTPError as exc:
                if may_retry_failures and attempt < self._max_retries:
                    await self._sleep(self._delay(attempt, None))
                    attempt += 1
                    continue
                raise NetworkError(
                    f"Cannot reach the service ({type(exc).__name__}).",
                    hint="Check your network connection.",
                ) from exc

            log.debug("%s %s -> %d (request %s)", method, path, response.status_code, request_id)
            if response.is_success:
                return response
            retryable = is_rate_limited(response) or (
                response.status_code in RETRYABLE_STATUS and may_retry_failures
            )
            if retryable and attempt < self._max_retries:
                wait = _retry_after(response)
                if wait is None or wait <= MAX_RETRY_AFTER:
                    await self._sleep(self._delay(attempt, wait))
                    attempt += 1
                    continue
            raise error_for(response, request_id)

    def _delay(self, attempt: int, retry_after: float | None) -> float:
        return compute_backoff(
            attempt, retry_after, self._backoff_base, self._max_backoff, self._jitter
        )
