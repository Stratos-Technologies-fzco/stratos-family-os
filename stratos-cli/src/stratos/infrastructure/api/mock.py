"""Mock transport for use until the real Platform API exists. Invents no API behaviour:
callers supply the routes and responses they need."""

from collections.abc import Callable, Mapping

import httpx

Route = httpx.Response | Callable[[httpx.Request], httpx.Response]


def mock_transport(routes: Mapping[tuple[str, str], Route]) -> httpx.MockTransport:
    """Build a transport from {(METHOD, path): response-or-handler}; unmatched routes return 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        route = routes.get((request.method, request.url.path))
        if route is None:
            return httpx.Response(404, json={"message": "not found"})
        return route(request) if callable(route) else route

    return httpx.MockTransport(handler)
