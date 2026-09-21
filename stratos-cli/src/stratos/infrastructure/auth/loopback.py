"""Loopback redirect receiver for the browser login (RFC 8252)."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qsl, urlsplit

from stratos.domain.exceptions import AuthenticationError

_PAGE = b"<html><body><h3>Stratos: sign-in complete.</h3>You can close this window.</body></html>"


class LoopbackReceiver:
    def __init__(self, timeout: float = 180.0) -> None:
        self._timeout = timeout
        self._params: dict[str, str] = {}
        self._done = threading.Event()
        self._server: HTTPServer | None = None

    def start(self) -> str:
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                query = urlsplit(self.path).query
                if urlsplit(self.path).path == "/callback" and not receiver._done.is_set():
                    receiver._params = dict(parse_qsl(query))
                    receiver._done.set()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_PAGE)

            def log_message(self, *args: object) -> None:  # keep the terminal quiet
                return

        self._server = HTTPServer(("127.0.0.1", 0), Handler)  # loopback only, ephemeral port
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_port}/callback"

    def wait(self) -> dict[str, str]:
        if not self._done.wait(self._timeout):
            raise AuthenticationError("Login timed out waiting for the browser.")
        return self._params

    def close(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
