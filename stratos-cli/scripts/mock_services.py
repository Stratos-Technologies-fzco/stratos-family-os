"""Local stand-ins for the services Stratos talks to, so development works offline.

Serves, on one port (default 8765):
  GET  /.well-known/openid-configuration  - a minimal OIDC discovery document
  GET  /v1/health                         - {"status": "ok"}
  GET  /v1/organisation                   - a fixed demo organisation
Anything else returns 404. There is no real Platform API yet; this only mimics what the
CLI's own probes and tests need. Run: python scripts/mock_services.py [port]
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
BASE = f"http://localhost:{PORT}"

ROUTES = {
    "/.well-known/openid-configuration": {
        "issuer": BASE,
        "authorization_endpoint": f"{BASE}/authorize",
        "token_endpoint": f"{BASE}/token",
        "jwks_uri": f"{BASE}/jwks",
    },
    "/v1/health": {"status": "ok"},
    "/v1/organisation": {"id": "demo", "name": "Demo Organisation"},
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = ROUTES.get(self.path)
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body if body is not None else {"error": "not found"}).encode())

    def log_message(self, format: str, *args: object) -> None:  # keep the console quiet
        return


if __name__ == "__main__":
    print(f"Mock services listening on {BASE} (Ctrl+C to stop)")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
