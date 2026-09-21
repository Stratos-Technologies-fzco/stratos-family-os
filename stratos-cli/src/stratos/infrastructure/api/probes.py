"""Connectivity probes for `stratos doctor --online`. No credentials are sent."""

import socket

import httpx


def http_probe(
    url: str,
    *,
    timeout: float = 5.0,
    any_response: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> tuple[bool, str]:
    """(reachable, detail). With `any_response`, any HTTP status counts as reachable."""
    try:
        with httpx.Client(timeout=timeout, transport=transport, follow_redirects=False) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        return False, f"unreachable ({type(exc).__name__})"
    if any_response or response.is_success:
        return True, f"reachable (HTTP {response.status_code})"
    return False, f"HTTP {response.status_code}"


def tcp_probe(host: str, port: int, *, timeout: float = 3.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port} reachable"
    except OSError as exc:
        return False, f"{host}:{port} unreachable ({type(exc).__name__})"
