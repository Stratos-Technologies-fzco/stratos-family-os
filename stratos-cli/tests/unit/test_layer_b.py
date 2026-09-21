import base64
import hashlib
import json
import threading
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from stratos.application.audit_service import AuditService
from stratos.application.auth_service import AuthService
from stratos.application.authorization import AuthorizationService
from stratos.domain.enums import AuditAction, AuditResult, Permission, Role
from stratos.domain.exceptions import (
    APIError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    DependencyError,
    NetworkError,
    ResourceNotFoundError,
    ValidationError,
)
from stratos.domain.models.auth import Identity, TokenSet
from stratos.infrastructure.api.client import PlatformApiClient
from stratos.infrastructure.api.mock import mock_transport
from stratos.infrastructure.auth.loopback import LoopbackReceiver
from stratos.infrastructure.auth.oidc import OidcProvider
from stratos.infrastructure.filesystem.audit_log import LocalAuditStore
from stratos.infrastructure.secrets import InMemorySecretStore, KeyringSecretStore
from stratos.utils import pkce
from stratos.utils.redaction import REDACTED
from stratos.utils.validation import require_secure_url, sanitize_text, validate_slug

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
ISSUER = "https://idp.example.com"


# =========================== M08: authentication ============================
def test_pkce_challenge_matches_rfc7636_example() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce.challenge_for(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    v = pkce.generate_verifier()
    assert 43 <= len(v) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    assert pkce.challenge_for(v) == expected


class FakeKeyring:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, key: str) -> str | None:
        return self.data.get((service, key))

    def set_password(self, service: str, key: str, value: str) -> None:
        self.data[(service, key)] = value

    def delete_password(self, service: str, key: str) -> None:
        self.data.pop((service, key), None)


def test_keyring_store_roundtrip_chunks_large_values_and_deletes() -> None:
    kr = FakeKeyring()
    store = KeyringSecretStore("stratos", backend=kr)
    big = "x" * 5000  # bigger than one Windows credential
    store.set("k", big)
    assert store.get("k") == big
    assert all(len(v) <= 900 or k[1].endswith("#n") for k, v in kr.data.items())
    store.set("k", "short")
    assert store.get("k") == "short"
    store.delete("k")
    assert store.get("k") is None and kr.data == {}


def test_keyring_failure_never_falls_back_to_plaintext() -> None:
    class Broken:
        def get_password(self, *a: str) -> str:
            raise RuntimeError("no backend")

    with pytest.raises(DependencyError):
        KeyringSecretStore(backend=Broken()).get("k")


class FakeIdp:
    def __init__(self) -> None:
        self.refreshed = 0
        self.fail_refresh = False
        self.challenge = ""

    def authorization_url(self, redirect_uri: str, state: str, code_challenge: str) -> str:
        self.challenge = code_challenge
        self.state = state
        return f"{ISSUER}/authorize?state={state}"

    def exchange_code(self, code: str, code_verifier: str, redirect_uri: str) -> TokenSet:
        assert pkce.challenge_for(code_verifier) == self.challenge
        return TokenSet(
            access_token=SecretStr("access-1"),
            refresh_token=SecretStr("refresh-1"),
            expires_at=NOW + timedelta(hours=1),
        )

    def refresh(self, refresh_token: str) -> TokenSet:
        if self.fail_refresh:
            raise AuthenticationError("refused")
        self.refreshed += 1
        return TokenSet(
            access_token=SecretStr("access-2"),
            refresh_token=SecretStr(refresh_token),
            expires_at=NOW + timedelta(hours=2),
        )

    def userinfo(self, access_token: str) -> Identity:
        return Identity(
            subject="u1", email="dev@example.com", organisation="acme", roles=("developer",)
        )


class FakeReceiver:
    def __init__(self, idp: FakeIdp, *, state: str | None = None, error: str | None = None) -> None:
        self.idp, self.state, self.error = idp, state, error
        self.closed = False

    def start(self) -> str:
        return "http://127.0.0.1:1/callback"

    def wait(self) -> dict[str, str]:
        if self.error:
            return {"error": self.error}
        return {"code": "abc", "state": self.state or self.idp.state}

    def close(self) -> None:
        self.closed = True


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def make_auth(
    store: InMemorySecretStore | None = None, **receiver_kwargs: str
) -> tuple[AuthService, FakeIdp, InMemorySecretStore, Clock, list[FakeReceiver]]:
    idp, store, clock = FakeIdp(), store or InMemorySecretStore(), Clock()
    receivers: list[FakeReceiver] = []

    def factory() -> FakeReceiver:
        r = FakeReceiver(idp, **receiver_kwargs)
        receivers.append(r)
        return r

    svc = AuthService(
        idp,
        store,
        issuer=ISSUER,
        receiver_factory=factory,
        open_browser=lambda url: None,
        clock=clock,
    )
    return svc, idp, store, clock, receivers


def test_login_stores_credentials_only_in_secret_store() -> None:
    svc, _, store, _, receivers = make_auth()
    identity = svc.login()
    assert identity.email == "dev@example.com" and receivers[0].closed
    assert store.get(f"{ISSUER}|access") == "access-1"
    assert svc.access_token() == "access-1"
    assert svc.whoami().organisation == "acme"


def test_login_rejects_bad_state_and_idp_errors() -> None:
    svc, *_ = make_auth(state="forged")
    with pytest.raises(AuthenticationError, match="state"):
        svc.login()
    svc, *_ = make_auth(error="access_denied")
    with pytest.raises(AuthenticationError, match="refused"):
        svc.login()


def test_expired_token_is_refreshed_and_saved() -> None:
    svc, idp, store, clock, _ = make_auth()
    svc.login()
    clock.now = NOW + timedelta(hours=1, minutes=5)
    assert svc.status().expired and svc.status().authenticated  # refreshable
    assert svc.access_token() == "access-2" and idp.refreshed == 1
    assert store.get(f"{ISSUER}|access") == "access-2"
    assert svc.whoami().subject == "u1"  # identity survives a refresh


def test_failed_refresh_clears_session() -> None:
    svc, idp, store, clock, _ = make_auth()
    svc.login()
    idp.fail_refresh = True
    clock.now = NOW + timedelta(hours=2)
    with pytest.raises(AuthenticationError, match="expired"):
        svc.access_token()
    assert store.get(f"{ISSUER}|access") is None


def test_logout_clears_credentials_and_status_redacts_tokens() -> None:
    svc, _, store, _, _ = make_auth()
    svc.login()
    dumped = svc.status().model_dump_json()
    assert "access-1" not in dumped and "refresh-1" not in dumped
    assert svc.logout() is True and svc.logout() is False
    assert store._data == {}
    assert not svc.status().authenticated
    with pytest.raises(AuthenticationError):
        svc.whoami()
    with pytest.raises(AuthenticationError):
        svc.access_token()


def test_token_set_never_prints_secrets() -> None:
    t = TokenSet(access_token=SecretStr("supersecret"))
    assert "supersecret" not in repr(t) and "supersecret" not in t.model_dump_json()


def _idp_transport(seen: dict[str, object]) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "userinfo_endpoint": f"{ISSUER}/userinfo",
                },
            )
        if path == "/token":
            seen["form"] = dict(httpx.QueryParams(req.content.decode()))
            if seen["form"]["grant_type"] == "authorization_code" and seen["form"]["code"] == "bad":  # type: ignore[index]
                return httpx.Response(
                    400, json={"error": "invalid_grant", "error_description": "code expired"}
                )
            return httpx.Response(
                200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
            )
        if path == "/userinfo":
            return httpx.Response(200, json={"sub": "u9", "email": "a@b.c", "roles": ["admin"]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_oidc_provider_flow() -> None:
    seen: dict[str, object] = {}
    idp = OidcProvider(
        ISSUER, "cid", http=httpx.Client(transport=_idp_transport(seen)), clock=lambda: NOW
    )
    url = idp.authorization_url("http://127.0.0.1:9/callback", "st", "chal")
    q = httpx.URL(url).params
    assert q["code_challenge_method"] == "S256" and q["state"] == "st" and q["client_id"] == "cid"
    tokens = idp.exchange_code("good", "ver", "http://127.0.0.1:9/callback")
    assert tokens.access_token.get_secret_value() == "at"
    assert tokens.expires_at == NOW + timedelta(hours=1)
    assert seen["form"]["code_verifier"] == "ver"  # type: ignore[index]
    assert idp.userinfo("at").roles == ("admin",)
    with pytest.raises(AuthenticationError, match="code expired"):
        idp.exchange_code("bad", "ver", "http://127.0.0.1:9/callback")
    # refresh keeps the old refresh token when the provider omits a new one
    assert idp.refresh("rt").refresh_token is not None


def test_oidc_requires_tls_issuer() -> None:
    with pytest.raises(ConfigurationError):
        OidcProvider("http://idp.example.com", "cid")


def test_loopback_receiver_gets_callback_and_times_out() -> None:
    r = LoopbackReceiver(timeout=5)
    uri = r.start()
    threading.Thread(
        target=lambda: urllib.request.urlopen(f"{uri}?code=c1&state=s1").read()
    ).start()
    try:
        assert r.wait() == {"code": "c1", "state": "s1"}
    finally:
        r.close()
    slow = LoopbackReceiver(timeout=0.05)
    slow.start()
    try:
        with pytest.raises(AuthenticationError, match="timed out"):
            slow.wait()
    finally:
        slow.close()


# =========================== M09: access control ============================
def ident(*roles: str) -> Identity:
    return Identity(subject="u", email="u@example.com", roles=roles)


@pytest.mark.parametrize(
    ("roles", "permission", "allowed"),
    [
        ((), Permission.ORG_READ, True),
        ((), Permission.PROJECT_CREATE, False),  # no role -> least privilege
        (("unknown-role",), Permission.PROJECT_CREATE, False),
        (("viewer",), Permission.REPO_CREATE, False),
        (("developer",), Permission.REPO_CREATE, True),
        (("developer",), Permission.PROJECT_DELETE, False),
        (("developer",), Permission.AUDIT_READ, False),
        (("maintainer",), Permission.AUDIT_READ, True),
        (("maintainer",), Permission.REPO_DELETE, False),
        (("ADMIN",), Permission.REPO_DELETE, True),
        (("viewer", "maintainer"), Permission.MCP_INSTALL, True),
    ],
)
def test_authorization_matrix(
    roles: tuple[str, ...], permission: Permission, allowed: bool
) -> None:
    svc = AuthorizationService()
    assert svc.is_allowed(ident(*roles), permission) is allowed
    if allowed:
        svc.require(ident(*roles), permission)
    else:
        with pytest.raises(AuthorizationError):
            svc.require(ident(*roles), permission)


def test_every_permission_is_held_by_admin_and_roles_are_nested() -> None:
    m = AuthorizationService()
    admin = m.permissions_for(ident("admin"))
    assert admin == frozenset(Permission)
    assert m.permissions_for(ident("viewer")) < m.permissions_for(ident("developer"))
    assert m.roles_for(ident()) == {Role.VIEWER}


def test_validation_and_sanitisation() -> None:
    assert validate_slug("my-repo.v2") == "my-repo.v2"
    for bad in ("", "UPPER", "-lead", "trail-", "a b", "x/../y", "a" * 101, "rm -rf;"):
        with pytest.raises(ValidationError):
            validate_slug(bad)
    assert sanitize_text("ok\x1b[31mred\x1b[0m\x07bell\x00") == "okredbell"
    assert sanitize_text("keeps\nnewlines\tand tabs") == "keeps\nnewlines\tand tabs"
    require_secure_url("https://a.example", "x")
    require_secure_url("http://localhost:8080", "x")
    with pytest.raises(ConfigurationError):
        require_secure_url("http://a.example", "x")


# =========================== M07: API client ================================
BASE = "https://api.example.com"


def client(routes: dict, **kw) -> tuple[PlatformApiClient, list[float]]:  # type: ignore[type-arg]
    sleeps: list[float] = []
    c = PlatformApiClient(
        BASE,
        kw.pop("token", lambda: "tok"),
        transport=mock_transport(routes),
        sleep=sleeps.append,
        jitter=lambda: 0.0,
        **kw,
    )
    return c, sleeps


def test_headers_auth_request_and_correlation_ids() -> None:
    seen: list[httpx.Request] = []

    def h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"ok": True})

    c, _ = client({("GET", "/v1/x"): h})
    assert c.get("/v1/x") == {"ok": True}
    c.get("/v1/x")
    r = seen[0]
    assert r.headers["Authorization"] == "Bearer tok"
    assert r.headers["X-Request-ID"] and r.headers["X-Correlation-ID"]
    assert seen[0].headers["X-Request-ID"] != seen[1].headers["X-Request-ID"]


def test_get_retries_5xx_with_exponential_backoff_then_succeeds() -> None:
    calls = iter([503, 503, 200])
    c, sleeps = client({("GET", "/a"): lambda r: httpx.Response(next(calls), json={"v": 1})})
    assert c.get("/a") == {"v": 1}
    assert sleeps == [0.5, 1.0]


def test_retries_are_bounded() -> None:
    n = {"c": 0}

    def h(req: httpx.Request) -> httpx.Response:
        n["c"] += 1
        return httpx.Response(503, json={"message": "down"})

    c, sleeps = client({("GET", "/a"): h}, max_retries=2)
    with pytest.raises(APIError, match="down"):
        c.get("/a")
    assert n["c"] == 3 and len(sleeps) == 2


def test_429_honours_retry_after_and_is_retried_for_post() -> None:
    calls = iter(
        [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(201, json={"id": 1})]
    )
    c, sleeps = client({("POST", "/a"): lambda r: next(calls)})
    assert c.post("/a", json={"n": 1}) == {"id": 1}
    assert sleeps == [7.0]


def test_excessive_retry_after_is_not_waited_for() -> None:
    c, sleeps = client({("GET", "/a"): httpx.Response(429, headers={"Retry-After": "3600"})})
    with pytest.raises(APIError):
        c.get("/a")
    assert sleeps == []


def test_destructive_calls_are_not_retried_without_idempotency_key() -> None:
    n = {"c": 0}

    def h(req: httpx.Request) -> httpx.Response:
        n["c"] += 1
        return httpx.Response(503)

    c, _ = client({("DELETE", "/a"): h, ("POST", "/b"): h})
    with pytest.raises(APIError):
        c.delete("/a")
    with pytest.raises(APIError):
        c.post("/b", json={})
    assert n["c"] == 2  # exactly one attempt each
    n["c"] = 0
    with pytest.raises(APIError):
        c.post("/b", json={}, idempotency_key="k1")
    assert n["c"] == 4  # retried because the caller supplied an idempotency key


@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (400, ValidationError),
        (401, AuthenticationError),
        (403, AuthorizationError),
        (404, ResourceNotFoundError),
        (422, ValidationError),
        (500, APIError),
    ],
)
def test_error_mapping(status: int, exc: type[Exception]) -> None:
    c, _ = client({("GET", "/a"): httpx.Response(status, json={"message": "nope"})})
    with pytest.raises(exc, match="request "):
        c.get("/a")


def test_error_messages_are_redacted() -> None:
    c, _ = client({("GET", "/a"): httpx.Response(500, json={"message": "failed token=abc123xyz"})})
    with pytest.raises(APIError) as info:
        c.get("/a")
    assert "abc123xyz" not in str(info.value) and REDACTED in str(info.value)


def test_network_failure_and_timeout_map_to_network_error() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    c, sleeps = client({("GET", "/a"): boom, ("POST", "/b"): boom}, max_retries=2)
    with pytest.raises(NetworkError):
        c.get("/a")
    assert len(sleeps) == 2  # safe method retried
    with pytest.raises(NetworkError):
        c.post("/b", json={})
    assert len(sleeps) == 2  # unsafe method not retried


def test_pagination_follows_link_header_and_respects_limit() -> None:
    def page1(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[1, 2], headers={"Link": f'<{BASE}/items?page=2>; rel="next"'}
        )

    def page2(req: httpx.Request) -> httpx.Response:
        assert req.url.params["page"] == "2"
        return httpx.Response(200, json=[3])

    routes = {("GET", "/items"): lambda r: page2(r) if "page" in r.url.params else page1(r)}
    c, _ = client(routes)
    assert list(c.paginate("/items")) == [1, 2, 3]
    assert list(c.paginate("/items", limit=2)) == [1, 2]


def test_pagination_refuses_other_hosts() -> None:
    evil = httpx.Response(
        200, json=[1], headers={"Link": '<https://evil.example/steal>; rel="next"'}
    )
    c, _ = client({("GET", "/items"): evil})
    with pytest.raises(APIError, match="different host"):
        list(c.paginate("/items"))


def test_client_requires_tls_and_valid_json() -> None:
    with pytest.raises(ConfigurationError):
        PlatformApiClient("http://api.example.com")
    c, _ = client({("GET", "/a"): httpx.Response(200, content=b"<html>")})
    with pytest.raises(APIError, match="JSON"):
        c.get("/a")


def test_unauthenticated_call_sends_no_token() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        assert "Authorization" not in req.headers
        return httpx.Response(200, json={})

    c, _ = client({("GET", "/public"): h})
    c.get("/public", authenticated=False)


# =========================== M10: audit =====================================
class Ids:
    def __init__(self) -> None:
        self.who: Identity | None = Identity(
            subject="u1", email="dev@example.com", organisation="acme", roles=("developer",)
        )

    def __call__(self) -> Identity | None:
        return self.who


def make_audit(tmp_path: Path) -> tuple[AuditService, LocalAuditStore, Ids]:
    ids = Ids()
    store = LocalAuditStore(tmp_path / "audit.jsonl")
    return AuditService(store, ids, clock=lambda: NOW, request_id=lambda: "req-1"), store, ids


def test_event_has_all_required_fields(tmp_path: Path) -> None:
    audit, store, _ = make_audit(tmp_path)
    e = audit.emit(AuditAction.REPO_CREATE, "repository", "acme/api", result=AuditResult.SUCCESS)
    got = store.get(e.id)
    assert got is not None
    assert (got.user, got.organisation, got.action, got.resource, got.resource_id) == (
        "dev@example.com", "acme", AuditAction.REPO_CREATE, "repository", "acme/api",
    )  # fmt: skip
    assert got.timestamp == NOW and got.result is AuditResult.SUCCESS and got.request_id == "req-1"


@pytest.mark.parametrize("action", list(AuditAction))
def test_every_sensitive_action_can_be_audited(tmp_path: Path, action: AuditAction) -> None:
    audit, _, _ = make_audit(tmp_path)
    with audit.record(action, "thing", "id-1"):
        pass
    assert audit.list_events(action=action)[0].result is AuditResult.SUCCESS


def test_failures_and_denials_are_audited_and_reraised(tmp_path: Path) -> None:
    audit, _, _ = make_audit(tmp_path)
    with pytest.raises(RuntimeError), audit.record(AuditAction.PROJECT_DELETE, "project", "p1"):
        raise RuntimeError("boom")
    with pytest.raises(AuthorizationError), audit.record(AuditAction.REPO_DELETE, "repo", "r1"):
        raise AuthorizationError("no")
    results = {e.action: e.result for e in audit.list_events()}
    assert results[AuditAction.PROJECT_DELETE] is AuditResult.FAILURE
    assert results[AuditAction.REPO_DELETE] is AuditResult.DENIED


def test_no_secrets_in_audit_events(tmp_path: Path) -> None:
    audit, _, _ = make_audit(tmp_path)
    audit.emit(
        AuditAction.MCP_INSTALL, "mcp", "srv",
        result=AuditResult.SUCCESS,
        details={"api_key": "sk-" + "a" * 30, "note": "token=hunter2hunter2", "name": "ok"},
    )  # fmt: skip
    raw = (tmp_path / "audit.jsonl").read_text()
    assert "hunter2" not in raw and "sk-aaaa" not in raw and "ok" in raw


def test_anonymous_events_and_queries(tmp_path: Path) -> None:
    audit, _, ids = make_audit(tmp_path)
    ids.who = None
    audit.emit(AuditAction.AGENT_RUN, "agent", None, result=AuditResult.FAILURE)
    ids.who = Identity(subject="u2", email="other@example.com")
    audit.emit(AuditAction.AGENT_RUN, "agent", None, result=AuditResult.SUCCESS)
    assert [e.user for e in audit.list_events()] == [
        "other@example.com",
        "anonymous",
    ]  # newest first
    assert len(audit.list_events(user="anonymous")) == 1 and len(audit.list_events(limit=1)) == 1
    with pytest.raises(ResourceNotFoundError):
        audit.get_event("missing")


def test_corrupt_audit_lines_are_skipped(tmp_path: Path) -> None:
    audit, _, _ = make_audit(tmp_path)
    audit.emit(AuditAction.SKILL_INSTALL, "skill", "s", result=AuditResult.SUCCESS)
    with (tmp_path / "audit.jsonl").open("a") as fh:
        fh.write("not json\n")
    assert len(audit.list_events()) == 1


# =========================== CLI wiring =====================================
runner = CliRunner()


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from stratos.config import loader
    from stratos.infrastructure import secrets as secrets_module
    from stratos.infrastructure.filesystem import audit_log

    monkeypatch.setattr(loader, "user_config_path", lambda: tmp_path / "user.yaml")
    monkeypatch.setattr(loader, "org_config_path", lambda: tmp_path / "org.yaml")
    monkeypatch.setattr(loader, "project_config_path", lambda cwd=None: tmp_path / "project.yaml")
    monkeypatch.setattr(audit_log, "default_audit_path", lambda: tmp_path / "audit.jsonl")
    shared = InMemorySecretStore()
    monkeypatch.setattr(secrets_module, "KeyringSecretStore", lambda *a, **k: shared)
    return tmp_path


def _app():  # type: ignore[no-untyped-def]
    from stratos.cli.app import app

    return app


def test_commands_require_configuration_exit_8(cli_env: Path) -> None:
    for argv in (["login"], ["whoami"], ["logout"], ["auth", "status"], ["audit", "list"]):
        r = runner.invoke(_app(), argv)
        assert r.exit_code == 8, (argv, r.output)


def test_not_logged_in_exit_3(cli_env: Path) -> None:
    runner.invoke(_app(), ["config", "set", "auth.issuer", ISSUER])
    runner.invoke(_app(), ["config", "set", "auth.client_id", "cid"])
    assert runner.invoke(_app(), ["whoami"]).exit_code == 3
    assert runner.invoke(_app(), ["audit", "list"]).exit_code == 3
    r = runner.invoke(_app(), ["-o", "json", "auth", "status"])
    assert r.exit_code == 0 and json.loads(r.output)["authenticated"] is False
    assert "not" in runner.invoke(_app(), ["logout"]).output


def test_help_lists_layer_b_commands_without_loading_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stratos.cli import context

    monkeypatch.setattr(
        context, "load_settings", lambda *a, **k: (_ for _ in ()).throw(AssertionError())
    )
    r = runner.invoke(_app(), ["--help"])
    assert r.exit_code == 0
    for name in ("login", "logout", "whoami", "auth", "audit"):
        assert name in r.output
    assert runner.invoke(_app(), ["auth", "--help"]).exit_code == 0
    assert runner.invoke(_app(), ["audit", "--help"]).exit_code == 0


def test_signed_in_developer_is_denied_audit_access(
    cli_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner.invoke(_app(), ["config", "set", "auth.issuer", ISSUER])
    runner.invoke(_app(), ["config", "set", "auth.client_id", "cid"])
    from stratos.infrastructure import secrets as secrets_module

    store = secrets_module.KeyringSecretStore()
    store.set(f"{ISSUER}|access", "tok-abcdefghijkl")
    store.set(
        f"{ISSUER}|meta",
        json.dumps(
            {
                "expires_at": None,
                "identity": {"subject": "u", "email": "d@x.io", "roles": ["developer"]},
            }
        ),
    )
    r = runner.invoke(_app(), ["whoami"])
    assert r.exit_code == 0 and "developer" in r.output
    assert runner.invoke(_app(), ["audit", "list"]).exit_code == 4
    masked = runner.invoke(_app(), ["auth", "token"])
    assert masked.exit_code == 0 and "tok-abcdefghijkl" not in masked.output
    revealed = runner.invoke(_app(), ["auth", "token", "--reveal"])
    assert "tok-abcdefghijkl" in revealed.output
    # promote to maintainer: audit access allowed
    store.set(
        f"{ISSUER}|meta",
        json.dumps(
            {
                "expires_at": None,
                "identity": {"subject": "u", "email": "d@x.io", "roles": ["maintainer"]},
            }
        ),
    )
    assert runner.invoke(_app(), ["audit", "list"]).exit_code == 0
    assert runner.invoke(_app(), ["audit", "get", "nope"]).exit_code == 5
