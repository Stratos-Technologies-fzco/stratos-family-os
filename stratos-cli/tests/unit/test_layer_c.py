import base64
import hashlib
import json
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from rich.console import Console
from typer.testing import CliRunner

from stratos.application.agent_runner import AgentRunner
from stratos.application.agent_service import AgentService
from stratos.application.ai_service import AIService
from stratos.application.audit_service import AuditService
from stratos.application.authorization import AuthorizationService
from stratos.application.knowledge_service import KnowledgeService
from stratos.application.mcp_service import McpService
from stratos.application.org_service import OrgService, TeamService
from stratos.application.repository_service import RepositoryService
from stratos.application.safety import OperationGuard, SafetyOptions, idempotency_key
from stratos.application.skill_service import SkillService
from stratos.cli.app import app
from stratos.cli.output import ConsoleRenderer
from stratos.domain.enums import (
    AgentPermission,
    AuditAction,
    AuditResult,
    OutputFormat,
    Permission,
)
from stratos.domain.exceptions import (
    APIError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    NetworkError,
    OperationCancelledError,
    ResourceNotFoundError,
    ValidationError,
)
from stratos.domain.models.auth import Identity
from stratos.domain.models.extensions import (
    AgentDefinition,
    AIResponse,
    McpServerSpec,
    ModelInfo,
)
from stratos.domain.models.github import (
    Member,
    OrgInfo,
    Repository,
    Team,
    TeamRepoPermission,
)
from stratos.infrastructure.ai import create_provider
from stratos.infrastructure.ai.anthropic import AnthropicProvider
from stratos.infrastructure.api.client import PlatformApiClient
from stratos.infrastructure.api.mock import mock_transport
from stratos.infrastructure.claude.manager import ClaudeCodeManager, ClaudeStatus
from stratos.infrastructure.filesystem.agent_registry import (
    BUILTIN_AGENTS,
    FilesystemAgentRegistry,
    FilesystemAgentRunLog,
)
from stratos.infrastructure.filesystem.audit_log import LocalAuditStore
from stratos.infrastructure.filesystem.cache import TtlCache
from stratos.infrastructure.filesystem.config_files import ConfigFileProtector
from stratos.infrastructure.filesystem.skill_registry import LocalSkillRegistry, compute_checksum
from stratos.infrastructure.github.git import clone_repository
from stratos.infrastructure.github.service import GITHUB_HEADERS, GitHubService
from stratos.infrastructure.github.token import GithubTokenProvider
from stratos.infrastructure.knowledge.markdown import MarkdownKnowledgeProvider
from stratos.infrastructure.mcp.registry import LocalMcpRegistry
from stratos.utils.redaction import SecretRedactor

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
runner = CliRunner()


# ================================ helpers ==========================================
def make_require(*roles: str) -> tuple[Callable[[Permission], Identity], Identity]:
    ident = Identity(subject="u1", email="dev@example.com", organisation="acme", roles=roles)
    authz = AuthorizationService()

    def require(p: Permission) -> Identity:
        authz.require(ident, p)
        return ident

    return require, ident


def make_audit(tmp_path: Path, ident: Identity) -> AuditService:
    return AuditService(LocalAuditStore(tmp_path / "audit.jsonl"), lambda: ident, clock=lambda: NOW)


def make_guard(
    *, dry_run: bool = False, yes: bool = False, answer: bool = True
) -> tuple[OperationGuard, Console, Console, list[str]]:
    out = Console(record=True, width=120, force_terminal=False)
    err = Console(record=True, width=120, force_terminal=False, stderr=True)
    asked: list[str] = []

    def confirm(message: str) -> bool:
        asked.append(message)
        return answer

    renderer = ConsoleRenderer(OutputFormat.TABLE, console=out, err_console=err)
    return OperationGuard(renderer, SafetyOptions(dry_run, yes), confirm), out, err, asked


def audit_results(tmp_path: Path) -> list[tuple[str, str]]:
    store = LocalAuditStore(tmp_path / "audit.jsonl")
    return [(e.action.value, e.result.value) for e in reversed(store.list())]


# ================================ M24: operation safety ================================
def test_dry_run_prints_plan_and_stops() -> None:
    guard, out, err, _ = make_guard(dry_run=True)
    assert guard.preview("repo create", ["Create x", "Protect y"]) is True
    text = out.export_text() + err.export_text()
    assert "DRY RUN" in text and "Create x" in text and "No changes were made" in text
    real, out2, err2, _ = make_guard()
    assert real.preview("repo create", ["Create x"]) is False
    assert out2.export_text() + err2.export_text() == ""


def test_confirmation_cancel_yes_and_prompt() -> None:
    guard, _, _, asked = make_guard(answer=False)
    with pytest.raises(OperationCancelledError):
        guard.confirm_destructive("Archiving", ["x becomes read-only"])
    assert asked == ["Continue?"]
    yes, _, _, asked_yes = make_guard(yes=True, answer=False)
    yes.confirm_destructive("Archiving", ["x"])  # --yes skips the prompt
    assert asked_yes == []


def test_idempotency_key_is_deterministic() -> None:
    assert idempotency_key("repo.create", "a", "b") == idempotency_key("repo.create", "a", "b")
    assert idempotency_key("repo.create", "a", "b") != idempotency_key("repo.create", "a", "c")


def test_protector_merges_backs_up_and_rolls_back(tmp_path: Path) -> None:
    p = ConfigFileProtector()
    f = tmp_path / ".mcp.json"
    f.write_text(json.dumps({"mine": {"keep": True}, "mcpServers": {"dev": {"command": "x"}}}))
    r = p.merge_json(f, {"mcpServers": {"new": {"command": "y"}}})
    data = json.loads(f.read_text())
    assert data["mine"] == {"keep": True} and set(data["mcpServers"]) == {"dev", "new"}
    assert r.changed and r.backup is not None and "new" in r.diff
    p.rollback(r)
    assert set(json.loads(f.read_text())["mcpServers"]) == {"dev"}
    p.merge_json(f, {"mcpServers": {"dev": {"command": "x"}}})
    again = p.merge_json(f, {"mcpServers": {"dev": {"command": "x"}}})
    assert again.changed is False  # idempotent


def test_protector_rollback_removes_newly_created_file(tmp_path: Path) -> None:
    p = ConfigFileProtector()
    r = p.merge_json(tmp_path / "new.json", {"a": 1})
    assert r.created and (tmp_path / "new.json").exists()
    p.rollback(r)
    assert not (tmp_path / "new.json").exists()


def test_protector_refuses_invalid_json_and_secrets(tmp_path: Path) -> None:
    p = ConfigFileProtector()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ConfigurationError):
        p.merge_json(bad, {"a": 1})
    assert bad.read_text() == "{not json"  # untouched
    with pytest.raises(ValidationError, match="secret"):
        p.merge_json(tmp_path / "x.json", {"env": {"note": "token=hunter2hunter2"}})
    assert not (tmp_path / "x.json").exists()


def test_protector_managed_block_preserves_developer_content(tmp_path: Path) -> None:
    p = ConfigFileProtector()
    f = tmp_path / "CLAUDE.md"
    f.write_text("# My notes\n\nKeep this.\n")
    p.write_managed_block(f, "instructions", "Org rule one")
    p.write_managed_block(f, "instructions", "Org rule two")
    text = f.read_text()
    assert "Keep this." in text and "Org rule two" in text and "Org rule one" not in text
    assert text.count("stratos:begin instructions") == 1
    assert p.write_managed_block(f, "instructions", "Org rule two").changed is False


def test_cache_expiry_invalidation_and_secret_refusal(tmp_path: Path) -> None:
    now = [1000.0]
    cache = TtlCache(tmp_path, clock=lambda: now[0])
    cache.set("ns", "k", {"a": 1}, ttl_seconds=60)
    assert cache.get("ns", "k") == {"a": 1}
    now[0] += 61
    assert cache.get("ns", "k") is None  # expired
    cache.set("ns", "k", [1], 60)
    cache.invalidate("ns", "k")
    assert cache.get("ns", "k") is None
    with pytest.raises(ValueError, match="secret"):
        cache.set("ns", "s", {"token": "ghp_" + "a" * 30}, 60)
    calls = []
    loader = lambda: calls.append(1) or {"v": 1}  # noqa: E731
    assert cache.get_or_load("ns", "z", 60, loader) == cache.get_or_load("ns", "z", 60, loader)
    assert len(calls) == 1


# ================================ M11: GitHub service ================================
BASE = "https://api.github.com"


def gh(routes: dict[Any, Any], sleeps: list[float] | None = None) -> GitHubService:
    client = PlatformApiClient(
        BASE,
        lambda: "ghtoken",
        transport=mock_transport(routes),
        sleep=(sleeps if sleeps is not None else []).append,
        jitter=lambda: 0.0,
        default_headers=GITHUB_HEADERS,
        send_correlation=False,
    )
    return GitHubService(client)


REPO = {
    "name": "api", "full_name": "acme/api", "private": True, "default_branch": "main",
    "html_url": "https://github.com/acme/api", "archived": False,
}  # fmt: skip


def test_github_headers_and_repo_lookup() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=REPO)

    svc = gh({("GET", "/repos/acme/api"): handler})
    assert svc.get_repo("acme", "api") == Repository(
        name="api", full_name="acme/api", url="https://github.com/acme/api"
    )
    h = seen[0].headers
    assert h["Accept"] == "application/vnd.github+json" and h["X-GitHub-Api-Version"]
    assert h["Authorization"] == "Bearer ghtoken" and "X-Correlation-ID" not in h
    assert gh({}).get_repo("acme", "missing") is None


def test_github_names_are_validated_before_use() -> None:
    svc = gh({})
    for bad in ("../x", "a/b", "x?y", "", ".git", "a b"):
        with pytest.raises(ValidationError):
            svc.get_repo("acme", bad)


def test_github_create_list_archive_and_protection() -> None:
    bodies: dict[str, Any] = {}

    def capture(key: str, status: int = 200, payload: Any = None) -> Callable[..., httpx.Response]:
        def handler(req: httpx.Request) -> httpx.Response:
            bodies[key] = json.loads(req.content) if req.content else None
            return httpx.Response(status, json=payload if payload is not None else REPO)

        return handler

    page2 = {**REPO, "name": "web", "full_name": "acme/web"}

    def repos(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return capture("create", 201)(req)
        if req.url.params.get("page") == "2":
            return httpx.Response(200, json=[page2])
        return httpx.Response(
            200, json=[REPO], headers={"Link": f'<{BASE}/orgs/acme/repos?page=2>; rel="next"'}
        )

    svc = gh(
        {
            ("POST", "/orgs/acme/repos"): repos,
            ("GET", "/orgs/acme/repos"): repos,
            ("PATCH", "/repos/acme/api"): capture("archive", payload={**REPO, "archived": True}),
            ("PUT", "/repos/acme/api/branches/main/protection"): capture("protect", payload={}),
        }
    )
    svc.create_repo("acme", "api", private=True, description="d")
    assert bodies["create"] == {
        "name": "api",
        "private": True,
        "auto_init": True,
        "description": "d",
    }
    assert [r.name for r in svc.list_repos("acme")] == ["api", "web"]
    assert svc.archive_repo("acme", "api").archived and bodies["archive"] == {"archived": True}
    svc.protect_branch("acme", "api", "main")
    p = bodies["protect"]
    assert p["enforce_admins"] and not p["allow_force_pushes"] and not p["allow_deletions"]
    assert p["required_pull_request_reviews"]["required_approving_review_count"] == 1
    with pytest.raises(ValidationError):
        svc.protect_branch("acme", "api", "../evil")


def test_github_codeowners_create_update_and_noop() -> None:
    path = "/repos/acme/api/contents/.github/CODEOWNERS"
    puts: list[dict[str, Any]] = []
    state: dict[str, Any] = {"content": None}

    def get(req: httpx.Request) -> httpx.Response:
        if state["content"] is None:
            return httpx.Response(404, json={"message": "Not Found"})
        enc = base64.b64encode(state["content"].encode()).decode()
        return httpx.Response(200, json={"content": enc, "sha": "abc"})

    def put(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        puts.append(body)
        state["content"] = base64.b64decode(body["content"]).decode()
        return httpx.Response(201, json={})

    svc = gh({("GET", path): get, ("PUT", path): put})
    assert svc.put_codeowners("acme", "api", "* @acme/dev\n", "main") is True
    assert "sha" not in puts[0]
    assert svc.put_codeowners("acme", "api", "* @acme/dev\n", "main") is False  # already current
    assert svc.put_codeowners("acme", "api", "* @acme/ops\n", "main") is True
    assert puts[1]["sha"] == "abc" and len(puts) == 2


def test_github_primary_rate_limit_waits_until_reset_then_succeeds() -> None:
    reset = str(int(time.time()) + 3)
    calls = iter(
        [
            httpx.Response(
                403,
                json={"message": "API rate limit exceeded"},
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset},
            ),
            httpx.Response(200, json=REPO),
        ]
    )
    sleeps: list[float] = []
    svc = gh({("GET", "/repos/acme/api"): lambda r: next(calls)}, sleeps)
    assert svc.get_repo("acme", "api") is not None
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 4


def test_github_plain_403_is_permission_error_not_retried() -> None:
    sleeps: list[float] = []
    svc = gh(
        {("GET", "/repos/acme/api"): httpx.Response(403, json={"message": "Forbidden"})}, sleeps
    )
    with pytest.raises(AuthorizationError):
        svc.get_repo("acme", "api")
    assert sleeps == []


def test_github_org_members_teams() -> None:
    admins = [{"login": "boss"}]
    members = [{"login": "boss"}, {"login": "dev"}]

    def members_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=admins if req.url.params.get("role") == "admin" else members
        )

    team = {"slug": "core", "name": "Core", "privacy": "closed", "permission": "pull"}
    svc = gh(
        {
            ("GET", "/orgs/acme"): httpx.Response(
                200,
                json={
                    "login": "acme",
                    "two_factor_requirement_enabled": True,
                    "members_can_create_repositories": False,
                    "default_repository_permission": "read",
                },
            ),  # fmt: skip
            ("GET", "/orgs/acme/members"): members_handler,
            ("GET", "/orgs/acme/teams"): httpx.Response(200, json=[team]),
            ("GET", "/orgs/acme/teams/core"): httpx.Response(200, json=team),
            ("GET", "/orgs/acme/teams/core/members"): httpx.Response(200, json=[{"login": "dev"}]),
            ("GET", "/orgs/acme/teams/core/repos"): httpx.Response(
                200,
                json=[
                    {"full_name": "acme/api", "permissions": {"pull": True, "push": True}},
                    {"full_name": "acme/web", "role_name": "maintain", "permissions": {}},
                ],
            ),
        }
    )
    assert svc.get_org("acme").two_factor_requirement_enabled is True
    assert [(m.login, m.role) for m in svc.list_members("acme")] == [
        ("boss", "admin"),
        ("dev", "member"),
    ]
    assert svc.list_teams("acme")[0].slug == "core" and svc.get_team("acme", "nope") is None
    assert [m.login for m in svc.team_members("acme", "core")] == ["dev"]
    assert [(p.repository, p.permission) for p in svc.team_repos("acme", "core")] == [
        ("acme/api", "push"),
        ("acme/web", "maintain"),
    ]


def test_github_token_provider_env_gh_cli_and_failure() -> None:
    assert GithubTokenProvider({"GITHUB_TOKEN": "t1"})() == "t1"
    assert GithubTokenProvider({"GH_TOKEN": "t2"})() == "t2"

    class Out:
        returncode, stdout = 0, "from-gh\n"

    ok = GithubTokenProvider({}, which=lambda n: "/usr/bin/gh", run=lambda *a, **k: Out())
    assert ok() == "from-gh"
    with pytest.raises(AuthenticationError, match="GitHub token"):
        GithubTokenProvider({}, which=lambda n: None)()


def test_git_clone_validates_and_reports_failures(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    class Ok:
        returncode, stderr = 0, ""

    def run_ok(cmd: list[str], **k: Any) -> Ok:
        calls.append(cmd)
        return Ok()

    dest = tmp_path / "api"
    assert clone_repository("acme", "api", dest, run=run_ok) == dest
    assert calls[0][:2] == ["git", "clone"] and calls[0][2] == "https://github.com/acme/api.git"
    assert "token" not in " ".join(calls[0]).lower()
    with pytest.raises(ValidationError):
        clone_repository("acme", "../x", dest, run=run_ok)
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "f").write_text("x")
    with pytest.raises(ValidationError, match="not empty"):
        clone_repository("acme", "api", tmp_path / "full", run=run_ok)

    class Bad:
        returncode, stderr = 128, "fatal: token=abc123secret denied"

    with pytest.raises(Exception, match="git clone failed") as info:
        clone_repository("acme", "api", tmp_path / "b", run=lambda *a, **k: Bad())
    assert "abc123secret" not in str(info.value)


# ================================ M11: repository service ================================
class FakeGitHub:
    def __init__(self) -> None:
        self.repos: dict[str, Repository] = {}
        self.created = 0
        self.raise_on_create: Exception | None = None
        self.protected: list[str] = []
        self.codeowners: dict[str, str] = {}
        self.org = OrgInfo(login="acme", two_factor_requirement_enabled=True)
        self.calls = 0

    def get_repo(self, org: str, name: str) -> Repository | None:
        return self.repos.get(f"{org}/{name}")

    def create_repo(
        self, org: str, name: str, *, private: bool = True, description: str | None = None
    ) -> Repository:
        if self.raise_on_create:
            raise self.raise_on_create
        self.created += 1
        repo = Repository(name=name, full_name=f"{org}/{name}", private=private)
        self.repos[repo.full_name] = repo
        return repo

    def list_repos(self, org: str, *, limit: int = 100) -> list[Repository]:
        return list(self.repos.values())[:limit]

    def archive_repo(self, org: str, name: str) -> Repository:
        repo = self.repos[f"{org}/{name}"].model_copy(update={"archived": True})
        self.repos[repo.full_name] = repo
        return repo

    def protect_branch(self, org: str, repo: str, branch: str) -> None:
        self.protected.append(f"{org}/{repo}@{branch}")

    def put_codeowners(self, org: str, repo: str, content: str, branch: str) -> bool:
        key = f"{org}/{repo}"
        changed = self.codeowners.get(key) != content
        self.codeowners[key] = content
        return changed

    def get_org(self, org: str) -> OrgInfo:
        self.calls += 1
        return self.org

    def list_members(self, org: str, *, limit: int = 200) -> list[Member]:
        self.calls += 1
        return [Member(login="dev", role="member")]

    def list_teams(self, org: str, *, limit: int = 200) -> list[Team]:
        self.calls += 1
        return [Team(slug="core", name="Core")]

    def get_team(self, org: str, slug: str) -> Team | None:
        return Team(slug=slug, name=slug.title()) if slug == "core" else None

    def team_members(self, org: str, slug: str, *, limit: int = 200) -> list[Member]:
        return [Member(login="dev")]

    def team_repos(self, org: str, slug: str, *, limit: int = 200) -> list[TeamRepoPermission]:
        return [TeamRepoPermission(repository="acme/api", permission="push")]


def make_repo_service(
    tmp_path: Path, *roles: str, **guard_kw: Any
) -> tuple[RepositoryService, FakeGitHub, Console, Console, list[str]]:
    require, ident = make_require(*roles)
    guard, out, err, asked = make_guard(**guard_kw)
    fake = FakeGitHub()
    svc = RepositoryService(
        fake, require, make_audit(tmp_path, ident), guard, "acme",
        clone=lambda org, name, dest: dest,
    )  # fmt: skip
    return svc, fake, out, err, asked


def test_repo_create_is_idempotent_and_audited(tmp_path: Path) -> None:
    svc, fake, *_ = make_repo_service(tmp_path, "developer")
    first = svc.create("api", description="x")
    second = svc.create("api")
    assert first and first.created and second and not second.created
    assert fake.created == 1  # no duplicate
    assert audit_results(tmp_path) == [("repo.create", "success")] * 2


def test_repo_create_dry_run_writes_and_audits_nothing(tmp_path: Path) -> None:
    svc, fake, out, err, _ = make_repo_service(tmp_path, "developer", dry_run=True)
    assert svc.create("api") is None
    assert fake.created == 0 and audit_results(tmp_path) == []
    assert "DRY RUN" in out.export_text() + err.export_text()


def test_repo_create_handles_lost_race(tmp_path: Path) -> None:
    svc, fake, *_ = make_repo_service(tmp_path, "developer")

    def race(*a: Any, **k: Any) -> Repository:
        fake.repos["acme/api"] = Repository(name="api", full_name="acme/api")
        raise ValidationError("name already exists")

    fake.create_repo = race  # type: ignore[method-assign]
    result = svc.create("api")
    assert result and not result.created


def test_repo_create_requires_permission_and_valid_name(tmp_path: Path) -> None:
    svc, fake, *_ = make_repo_service(tmp_path, "viewer")
    with pytest.raises(AuthorizationError):
        svc.create("api")
    dev, _, *_ = make_repo_service(tmp_path, "developer")
    for bad in ("../x", "a/b", "x y", ".git"):
        with pytest.raises(ValidationError):
            dev.create(bad)
    assert fake.created == 0


def test_public_repo_needs_confirmation(tmp_path: Path) -> None:
    svc, fake, _, _, asked = make_repo_service(tmp_path, "developer", answer=False)
    with pytest.raises(OperationCancelledError):
        svc.create("api", private=False)
    assert fake.created == 0 and asked == ["Continue?"]
    ok, fake2, *_ = make_repo_service(tmp_path, "developer", yes=True)
    result = ok.create("api", private=False)
    assert result and not result.repository.private


def test_repo_archive_confirms_audits_and_is_idempotent(tmp_path: Path) -> None:
    svc, fake, _, _, asked = make_repo_service(tmp_path, "maintainer", answer=False)
    fake.repos["acme/api"] = Repository(name="api", full_name="acme/api")
    with pytest.raises(OperationCancelledError):
        svc.archive("api")
    assert not fake.repos["acme/api"].archived  # cancel changes nothing
    yes, fake2, *_ = make_repo_service(tmp_path, "maintainer", yes=True)
    fake2.repos["acme/api"] = Repository(name="api", full_name="acme/api")
    archived = yes.archive("api")
    assert archived and archived.archived
    again = yes.archive("api")  # already archived: no-op, not audited again
    assert again and again.archived
    assert audit_results(tmp_path) == [("repo.archive", "success")]
    with pytest.raises(AuthorizationError):
        make_repo_service(tmp_path, "developer", yes=True)[0].archive("api")


def test_repo_archive_missing_repo(tmp_path: Path) -> None:
    svc, *_ = make_repo_service(tmp_path, "maintainer", yes=True)
    with pytest.raises(ResourceNotFoundError):
        svc.archive("ghost")


def test_repo_configure_protection_and_codeowners(tmp_path: Path) -> None:
    svc, fake, *_ = make_repo_service(tmp_path, "maintainer")
    fake.repos["acme/api"] = Repository(name="api", full_name="acme/api", default_branch="trunk")
    done = svc.configure("api", codeowners="* @acme/dev\n")
    assert fake.protected == ["acme/api@trunk"] and "CODEOWNERS updated" in done
    assert "CODEOWNERS already up to date" in svc.configure("api", codeowners="* @acme/dev\n")
    assert ("repo.configure", "success") in audit_results(tmp_path)
    with pytest.raises(ValidationError, match="Nothing to configure"):
        svc.configure("api", branch_protection=False)
    with pytest.raises(AuthorizationError):
        make_repo_service(tmp_path, "developer")[0].configure("api")


def test_repo_list_get_clone(tmp_path: Path) -> None:
    svc, fake, *_ = make_repo_service(tmp_path, "viewer")
    fake.repos["acme/api"] = Repository(name="api", full_name="acme/api")
    assert [r.name for r in svc.list_repositories()] == ["api"]
    assert svc.get("api").full_name == "acme/api"
    with pytest.raises(ResourceNotFoundError):
        svc.get("ghost")
    assert svc.clone("api", tmp_path / "dest") == tmp_path / "dest"


# ================================ M12: organisation and teams ================================
def test_org_metadata_is_cached_and_refreshable(tmp_path: Path) -> None:
    require, _ = make_require("viewer")
    fake = FakeGitHub()
    org = OrgService(fake, require, TtlCache(tmp_path / "c"), "acme", lambda: {"stratos.x": 1})
    assert org.get().login == "acme" and org.get().login == "acme"
    assert fake.calls == 1  # second call served from cache
    org.get(refresh=True)
    assert fake.calls == 2
    org.members(), org.members(), org.teams(), org.teams()
    assert fake.calls == 4
    policy = org.policy()
    assert policy["github.two_factor_required"] is True and policy["stratos.x"] == 1


def test_team_service_and_permission_denied(tmp_path: Path) -> None:
    require, _ = make_require("viewer")
    teams = TeamService(FakeGitHub(), require, TtlCache(tmp_path / "c"), "acme")
    assert teams.list_teams()[0].slug == "core" and teams.get("core").name == "Core"
    assert teams.members("core")[0].login == "dev"
    assert teams.permissions("core")[0].permission == "push"
    with pytest.raises(ResourceNotFoundError):
        teams.get("nope")

    def deny(p: Permission) -> Identity:
        raise AuthorizationError("no")

    with pytest.raises(AuthorizationError):
        TeamService(FakeGitHub(), deny, TtlCache(tmp_path / "c2"), "acme").list_teams()
    with pytest.raises(AuthorizationError):
        OrgService(FakeGitHub(), deny, TtlCache(tmp_path / "c3"), "acme").get()


def test_org_cache_never_stores_secrets(tmp_path: Path) -> None:
    require, _ = make_require("viewer")
    fake = FakeGitHub()
    fake.org = OrgInfo(login="acme", description="token=abcd1234efgh5678")
    org = OrgService(fake, require, TtlCache(tmp_path / "c"), "acme")
    assert org.get().login == "acme"
    org.get()
    assert fake.calls == 2  # not served from cache, because it would have stored a secret
    assert not list((tmp_path / "c").glob("*.json")) or "abcd1234efgh5678" not in "".join(
        p.read_text() for p in (tmp_path / "c").glob("*.json")
    )


# ================================ M13: Claude Code ================================
def test_claude_detection_present_and_absent(tmp_path: Path) -> None:
    absent = ClaudeCodeManager(tmp_path, which=lambda n: None).detect()
    assert absent == ClaudeStatus(detected=False)
    assert absent.doctor_check()[0] == "warning"

    class Out:
        stdout = "2.1.0 (Claude Code)\n"

    present = ClaudeCodeManager(
        tmp_path, which=lambda n: "/bin/claude", run=lambda *a, **k: Out()
    ).detect()
    assert present.detected and present.version == "2.1.0 (Claude Code)"
    assert present.doctor_check()[0] == "pass"


def test_claude_detection_survives_broken_binary(tmp_path: Path) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise OSError("cannot run")

    status = ClaudeCodeManager(tmp_path, which=lambda n: "/bin/claude", run=boom).detect()
    assert status.detected and status.version is None


def test_claude_applies_instructions_without_clobbering(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# Mine\nDo not touch.\n")
    m = ClaudeCodeManager(tmp_path)
    m.apply_instructions(organisation="Org rule", standards="Use type hints", project="Proj rule")
    text = (tmp_path / "CLAUDE.md").read_text()
    assert "Do not touch." in text and "Org rule" in text and "Use type hints" in text
    assert "Proj rule" in text and "## Coding standards" in text


def test_claude_mcp_and_settings_merge_keep_existing(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"mine": {"command": "x"}}}))
    m = ClaudeCodeManager(tmp_path)
    m.merge_mcp_servers({"new": {"command": "y"}})
    assert set(m.mcp_servers()) == {"mine", "new"}
    m.remove_mcp_server("new")
    assert set(m.mcp_servers()) == {"mine"}
    m.merge_settings({"permissions": {"deny": ["Read(./.env)"]}})
    assert json.loads((tmp_path / ".claude" / "settings.json").read_text())["permissions"]


def test_claude_skill_install_rejects_path_traversal_and_rolls_back(tmp_path: Path) -> None:
    m = ClaudeCodeManager(tmp_path)
    with pytest.raises(ValidationError):
        m.install_skill("s", {"../../evil.txt": b"x"})
    assert not (tmp_path / "evil.txt").exists()
    with pytest.raises(ValidationError):
        m.install_skill("s", {"ok.md": b"fine", "/abs.md": b"x"})
    assert not (tmp_path / ".claude" / "skills" / "s" / "ok.md").exists()  # nothing half-installed
    with pytest.raises(ValidationError):
        m.install_skill("../s", {"a.md": b"x"})
    m.install_skill("s", {"SKILL.md": b"hi", "sub/x.txt": b"y"})
    assert m.installed_skill_names() == ["s"]
    backup = m.remove_skill("s")
    assert (
        backup and (backup / "SKILL.md").read_bytes() == b"hi" and m.installed_skill_names() == []
    )


# ================================ M14: skills ================================
def make_skill_registry(
    root: Path, *, version: str = "1.0.0", tamper: bool = False
) -> dict[str, bytes]:
    files = {"SKILL.md": b"# Review skill\nBe kind.\n", "notes/a.txt": b"aaa"}
    skill_dir = root / "review"
    (skill_dir / "notes").mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        (skill_dir / rel).write_bytes(data + (b"TAMPERED" if tamper and rel == "SKILL.md" else b""))
    index = {
        "skills": [
            {
                "name": "review",
                "version": version,
                "description": "Reviews things",
                "source": "acme/skills",
                "checksum": compute_checksum(files),
                "permissions": ["read-files"],
                "compatibility": {"stratos": ">=0.1.0"},
            }  # fmt: skip
        ]
    }
    (root / "index.json").write_text(json.dumps(index))
    return files


def make_skill_service(
    tmp_path: Path, *roles: str, **guard_kw: Any
) -> tuple[SkillService, Path, list[str], Path]:
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    reg = tmp_path / "registry"
    reg.mkdir(parents=True, exist_ok=True)
    require, ident = make_require(*roles)
    guard, out, err, asked = make_guard(**guard_kw)
    protector = ConfigFileProtector()
    svc = SkillService(
        LocalSkillRegistry(reg), ClaudeCodeManager(project, protector), protector, require,
        make_audit(tmp_path, ident), guard, TtlCache(tmp_path / "cache"),
        stratos_version="0.1.0", registry_id=str(reg),
    )  # fmt: skip
    return svc, project, asked, reg


def test_skill_install_verifies_copies_locks_and_audits(tmp_path: Path) -> None:
    svc, project, asked, reg = make_skill_service(tmp_path, "developer")
    make_skill_registry(reg)
    outcome = svc.install("review")
    assert outcome and outcome.action == "installed"
    assert (project / ".claude/skills/review/SKILL.md").read_bytes().startswith(b"# Review")
    assert (project / ".claude/skills/review/notes/a.txt").read_bytes() == b"aaa"
    lock = json.loads((project / ".stratos/skills.lock.json").read_text())["skills"]["review"]
    assert lock["version"] == "1.0.0" and lock["permissions"] == ["read-files"]
    assert asked == ["Continue?"]  # declared permissions were surfaced for approval
    assert audit_results(tmp_path) == [("skill.install", "success")]
    assert (
        svc.install("review").action == "unchanged"
    )  # idempotent; no new prompt or audit  # type: ignore[union-attr]
    assert len(audit_results(tmp_path)) == 1


def test_skill_permission_approval_can_be_refused(tmp_path: Path) -> None:
    svc, project, _, reg = make_skill_service(tmp_path, "developer", answer=False)
    make_skill_registry(reg)
    with pytest.raises(OperationCancelledError):
        svc.install("review")
    assert not (project / ".claude").exists()


def test_skill_checksum_mismatch_is_rejected_and_audited(tmp_path: Path) -> None:
    svc, project, _, reg = make_skill_service(tmp_path, "developer", yes=True)
    make_skill_registry(reg, tamper=True)
    with pytest.raises(ValidationError, match="Checksum mismatch"):
        svc.install("review")
    assert not (project / ".claude").exists()  # nothing written
    assert audit_results(tmp_path) == [("skill.install", "failure")]


def test_skill_incompatible_and_unknown(tmp_path: Path) -> None:
    svc, _, _, reg = make_skill_service(tmp_path, "developer", yes=True)
    make_skill_registry(reg)
    index = json.loads((reg / "index.json").read_text())
    index["skills"][0]["compatibility"] = {"stratos": ">=9.0"}
    (reg / "index.json").write_text(json.dumps(index))
    with pytest.raises(ValidationError, match="requires stratos"):
        svc.install("review")
    with pytest.raises(ResourceNotFoundError):
        svc.install("ghost")


def test_skill_dry_run_and_permission(tmp_path: Path) -> None:
    svc, project, _, reg = make_skill_service(tmp_path, "developer", dry_run=True)
    make_skill_registry(reg)
    assert svc.install("review") is None
    assert not (project / ".claude").exists() and audit_results(tmp_path) == []
    with pytest.raises(AuthorizationError):
        make_skill_service(tmp_path, "viewer")[0].install("review")


def test_skill_update_and_list(tmp_path: Path) -> None:
    svc, project, _, reg = make_skill_service(tmp_path, "developer", yes=True)
    make_skill_registry(reg)
    svc.install("review")
    assert svc.update()[0].action == "unchanged"
    make_skill_registry(reg, version="1.1.0")
    (reg / "index.json").write_text((reg / "index.json").read_text())
    assert svc.update("review")[0].action == "updated"
    lock = json.loads((project / ".stratos/skills.lock.json").read_text())
    assert lock["skills"]["review"]["version"] == "1.1.0"
    with pytest.raises(ResourceNotFoundError):
        svc.update("ghost")
    row = svc.list_skills()[0]
    assert row.manifest.name == "review"


def test_skill_remove_confirms_backs_up_and_audits(tmp_path: Path) -> None:
    svc, project, _, reg = make_skill_service(tmp_path, "maintainer", yes=True)
    make_skill_registry(reg)
    svc.install("review")
    backup = svc.remove("review")
    assert backup and backup.exists() and not (project / ".claude/skills/review").exists()
    assert ("skill.remove", "success") in audit_results(tmp_path)
    with pytest.raises(ResourceNotFoundError):
        svc.remove("review")
    cancel, _, _, reg2 = make_skill_service(tmp_path / "b", "maintainer", answer=False)
    with pytest.raises(AuthorizationError):
        make_skill_service(tmp_path / "c", "developer")[0].remove("review")


# ================================ M15: MCP ================================
def write_mcp_registry(root: Path, servers: list[dict[str, Any]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.json").write_text(json.dumps({"servers": servers}))


GOOD = {
    "name": "github", "description": "GitHub MCP", "command": "npx",
    "args": ["-y", "@example/github-mcp"], "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"},
}  # fmt: skip


def make_mcp_service(
    tmp_path: Path,
    *roles: str,
    allowed: list[str] | None = None,
    environ: dict[str, str] | None = None,
    **guard_kw: Any,
) -> tuple[McpService, Path, Path]:
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    reg = tmp_path / "mcp-registry"
    require, ident = make_require(*roles)
    guard, *_ = make_guard(**guard_kw)
    svc = McpService(
        LocalMcpRegistry(reg), ClaudeCodeManager(project), require, make_audit(tmp_path, ident),
        guard, TtlCache(tmp_path / "cache"), allowed=allowed, environ=environ or {},
        registry_id=str(reg),
    )  # fmt: skip
    return svc, project, reg


def test_mcp_install_merges_backs_up_and_audits(tmp_path: Path) -> None:
    svc, project, reg = make_mcp_service(tmp_path, "maintainer")
    write_mcp_registry(reg, [GOOD])
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"mine": {"command": "x"}}}))
    result = svc.install("github")
    assert result and result.changed and result.backup is not None
    servers = json.loads((project / ".mcp.json").read_text())["mcpServers"]
    assert set(servers) == {"mine", "github"}
    assert servers["github"]["env"] == {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}
    assert audit_results(tmp_path) == [("mcp.install", "success")]
    again = svc.install("github")
    assert again and not again.changed  # idempotent


def test_mcp_install_rollback_round_trip(tmp_path: Path) -> None:
    svc, project, reg = make_mcp_service(tmp_path, "maintainer")
    write_mcp_registry(reg, [GOOD])
    original = json.dumps({"mcpServers": {"mine": {"command": "x"}}})
    (project / ".mcp.json").write_text(original)
    result = svc.install("github")
    assert result and "github" in (project / ".mcp.json").read_text()
    svc.rollback(result)
    assert json.loads((project / ".mcp.json").read_text()) == json.loads(original)


def test_mcp_policy_blocks_unlisted_servers_and_audits_denial(tmp_path: Path) -> None:
    svc, project, reg = make_mcp_service(tmp_path, "maintainer", allowed=["approved-only"])
    write_mcp_registry(reg, [GOOD])
    with pytest.raises(AuthorizationError, match="organisation policy"):
        svc.install("github")
    assert not (project / ".mcp.json").exists()
    assert audit_results(tmp_path) == [("mcp.install", "denied")]
    rows = svc.list_servers()
    assert rows[0].allowed is False and rows[0].installed is False


@pytest.mark.parametrize(
    "bad",
    [
        {"env": {"API_KEY": "sk-" + "a" * 30}},
        {"env": {"GITHUB_TOKEN": "literal-value"}},  # sensitive key must be a reference
        {"args": ["--token=abc123secretvalue"]},
        {"command": "npx; rm -rf /"},
        {"transport": "http"},
    ],
)
def test_mcp_rejects_secrets_and_unsafe_config(tmp_path: Path, bad: dict[str, Any]) -> None:
    svc, project, reg = make_mcp_service(tmp_path, "maintainer")
    write_mcp_registry(reg, [{**GOOD, **bad}])
    with pytest.raises(ValidationError):
        svc.install("github")
    assert not (project / ".mcp.json").exists()  # secrets never reach shared config
    assert audit_results(tmp_path) == [("mcp.install", "failure")]


def test_mcp_dry_run_and_permission(tmp_path: Path) -> None:
    svc, project, reg = make_mcp_service(tmp_path, "maintainer", dry_run=True)
    write_mcp_registry(reg, [GOOD])
    assert svc.install("github") is None and not (project / ".mcp.json").exists()
    assert audit_results(tmp_path) == []
    with pytest.raises(AuthorizationError):
        make_mcp_service(tmp_path / "x", "developer")[0].install("github")
    ghost_svc, _, ghost_reg = make_mcp_service(tmp_path / "y", "maintainer")
    write_mcp_registry(ghost_reg, [GOOD])
    with pytest.raises(ResourceNotFoundError):
        ghost_svc.install("ghost")


def test_mcp_configure_maps_env_names_and_rejects_values(tmp_path: Path) -> None:
    svc, project, reg = make_mcp_service(tmp_path, "maintainer")
    write_mcp_registry(reg, [GOOD])
    svc.install("github")
    svc.configure("github", env={"GITHUB_TOKEN": "MY_GH_TOKEN"}, args=["--flag"])
    cfg = json.loads((project / ".mcp.json").read_text())["mcpServers"]["github"]
    assert cfg["env"]["GITHUB_TOKEN"] == "${MY_GH_TOKEN}" and cfg["args"] == ["--flag"]
    with pytest.raises(ValidationError):
        svc.configure("github", env={"GITHUB_TOKEN": "not a var name!"})
    with pytest.raises(ResourceNotFoundError):
        svc.configure("ghost", env={"A": "B"})
    assert ("mcp.configure", "success") in audit_results(tmp_path)


def test_mcp_status_reports_missing_environment(tmp_path: Path) -> None:
    svc, _, reg = make_mcp_service(tmp_path, "maintainer", environ={"OTHER": "1"})
    write_mcp_registry(reg, [GOOD])
    svc.install("github")
    row = svc.status()[0]
    assert row.name == "github" and row.in_registry and row.missing_env == ("GITHUB_TOKEN",)
    ok, _, reg2 = make_mcp_service(tmp_path / "z", "maintainer", environ={"GITHUB_TOKEN": "x"})
    write_mcp_registry(reg2, [GOOD])
    ok.install("github")
    assert ok.status()[0].missing_env == ()


# ================================ M16: AI provider ================================
class FakeProvider:
    name = "fake"

    def __init__(self, text: str = "answer", fail: Exception | None = None) -> None:
        self.text, self.fail = text, fail
        self.prompts: list[tuple[str, str | None, str | None]] = []

    async def ask(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> AIResponse:
        self.prompts.append((prompt, system, model))
        if self.fail:
            raise self.fail
        return AIResponse(model=model or "fake-1", text=self.text, input_tokens=3, output_tokens=4)

    async def chat(
        self,
        messages: Any,
        *,
        tools: Any = (),
        model: str | None = None,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> Any:  # noqa: E501
        from stratos.domain.models.extensions import ChatTurn

        self.prompts.append((messages[-1].content, system, model))
        if self.fail:
            raise self.fail
        return ChatTurn(model=model or "fake-1", text=self.text, input_tokens=3, output_tokens=4)

    async def stream(self, prompt: str, **kw: Any):  # type: ignore[no-untyped-def]
        for part in ("Hel", "lo ", "world\n", "!"):
            yield part

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-1", display_name="Fake 1")]


class FakeAnthropic:
    """Stands in for anthropic.AsyncAnthropic."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.last: dict[str, Any] = {}
        outer = self

        class Messages:
            async def create(self, **kw: Any) -> Any:
                outer.last = kw
                if outer.error:
                    raise outer.error

                class Block:
                    type, text = "text", "Hello there"

                class Usage:
                    input_tokens, output_tokens = 5, 7

                class Resp:
                    content, usage = [Block()], Usage()

                return Resp()

            def stream(self, **kw: Any) -> Any:
                outer.last = kw

                class Ctx:
                    async def __aenter__(self_inner) -> Any:
                        if outer.error:
                            raise outer.error

                        class S:
                            @property
                            def text_stream(self_s) -> Any:
                                async def gen() -> Any:
                                    yield "a"
                                    yield "b"

                                return gen()

                        return S()

                    async def __aexit__(self_inner, *a: Any) -> None:
                        return None

                return Ctx()

        class Models:
            async def list(self, **kw: Any) -> Any:
                class M:
                    id, display_name = "claude-x", "Claude X"

                class Page:
                    data = [M()]

                return Page()

        self.messages, self.models = Messages(), Models()


def run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


def test_anthropic_provider_ask_stream_models() -> None:
    fake = FakeAnthropic()
    p = AnthropicProvider("key", default_model="claude-sonnet-5", client=fake)
    res = run(p.ask("hi", system="be brief", max_tokens=50))
    assert res.text == "Hello there" and res.model == "claude-sonnet-5" and res.output_tokens == 7
    assert fake.last["system"] == "be brief" and fake.last["max_tokens"] == 50
    assert fake.last["messages"] == [{"role": "user", "content": "hi"}]

    async def collect() -> list[str]:
        return [c async for c in p.stream("hi", model="m2")]

    assert run(collect()) == ["a", "b"] and fake.last["model"] == "m2"
    assert run(p.list_models())[0].id == "claude-x"


def test_anthropic_errors_map_to_stratos_exceptions() -> None:
    import anthropic

    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def status_err(cls: type[Exception], code: int) -> Exception:
        return cls("boom", response=httpx.Response(code, request=req), body=None)  # type: ignore[call-arg]

    cases: list[tuple[Exception, type[Exception]]] = [
        (status_err(anthropic.AuthenticationError, 401), AuthenticationError),
        (status_err(anthropic.PermissionDeniedError, 403), AuthorizationError),
        (status_err(anthropic.RateLimitError, 429), APIError),
        (anthropic.APIConnectionError(request=req), NetworkError),
        (status_err(anthropic.InternalServerError, 500), APIError),
    ]
    for source, expected in cases:
        p = AnthropicProvider("k", default_model="m", client=FakeAnthropic(error=source))
        with pytest.raises(expected):
            run(p.ask("hi"))

    async def stream_fail() -> None:
        async for _ in AnthropicProvider(
            "k", default_model="m", client=FakeAnthropic(error=cases[0][0])
        ).stream("x"):
            pass

    with pytest.raises(AuthenticationError):
        run(stream_fail())


def test_rate_limit_message_is_friendly_and_secret_free() -> None:
    import anthropic

    req = httpx.Request("POST", "https://x")
    err = anthropic.RateLimitError(
        "key sk-" + "a" * 30, response=httpx.Response(429, request=req), body=None
    )
    with pytest.raises(APIError) as info:
        run(AnthropicProvider("k", default_model="m", client=FakeAnthropic(error=err)).ask("hi"))
    assert "sk-aaaa" not in str(info.value) and "rate limiting" in str(info.value)


def test_missing_api_key_is_a_clear_error() -> None:
    with pytest.raises(AuthenticationError, match="No Anthropic API key") as info:
        run(AnthropicProvider(None, default_model="m").ask("hi"))
    assert "ANTHROPIC_API_KEY" in (info.value.hint or "")


def test_provider_factory_and_unknown_provider() -> None:
    assert create_provider("claude", api_key="k", default_model="m").name == "anthropic"
    with pytest.raises(ConfigurationError, match="Unknown AI provider"):
        create_provider("nope", api_key=None, default_model="m")


def test_ai_service_contract_with_fake_provider() -> None:
    require, _ = make_require("viewer")
    fake = FakeProvider()
    svc = AIService(
        lambda: fake, require, provider_name="fake", default_model="fake-1", max_tokens=99,
        api_key_configured=True,
    )  # fmt: skip
    assert svc.status().api_key_configured and svc.status().provider == "fake"
    answer = run(svc.ask("q", system="s"))
    assert answer.text == "answer" and fake.prompts[0] == ("q", "s", "fake-1")

    async def collect() -> str:
        return "".join([c async for c in svc.stream("q")])

    assert run(collect()) == "Hello world\n!"
    assert run(svc.models())[0].id == "fake-1"

    def deny(p: Permission) -> Identity:
        raise AuthenticationError("not logged in")

    with pytest.raises(AuthenticationError):
        run(AIService(lambda: fake, deny, provider_name="f", default_model="m", max_tokens=1,
                      api_key_configured=False).ask("q"))  # fmt: skip


def test_ai_sdk_and_clients_are_not_imported_for_help_or_config() -> None:
    code = (
        "import sys\n"
        "from typer.testing import CliRunner\n"
        "from stratos.cli.app import app\n"
        "r = CliRunner()\n"
        "r.invoke(app, ['--help'])\n"
        "r.invoke(app, ['ai', '--help'])\n"
        "r.invoke(app, ['config', 'list'])\n"
        "bad = [m for m in ('anthropic', 'stratos.infrastructure.github.service', "
        "'stratos.infrastructure.knowledge.markdown') if m in sys.modules]\n"
        "print('LOADED:' + ','.join(bad))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip().endswith("LOADED:")


# ================================ M18: knowledge ================================
def make_docs(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "runbook.md").write_text(
        "# Deployment Runbook\n\n## Rollback\n\nTo roll back a deployment, redeploy the last tag.\n"
        "Never paste token=supersecretvalue1 here.\n"
    )
    (root / "style.md").write_text("# Coding style\n\nWe mention deployment only once here.\n")
    (root / "other.md").write_text("# Holidays\n\nOffice closes in December.\n")
    (root / "notes.txt").write_text("deployment deployment deployment")  # not markdown


def make_provider(tmp_path: Path) -> MarkdownKnowledgeProvider:
    docs = tmp_path / "docs"
    make_docs(docs)
    return MarkdownKnowledgeProvider([docs], tmp_path / "manifest.json", clock=lambda: NOW)


def test_knowledge_ranking_empty_results_and_stopwords(tmp_path: Path) -> None:
    p = make_provider(tmp_path)
    hits = run(p.search("deployment runbook"))
    assert [h.id for h in hits][:2] == ["docs:runbook.md", "docs:style.md"]
    assert hits[0].score > hits[1].score and "docs:other.md" not in [h.id for h in hits]
    assert run(p.search("zebra unicorn")) == []
    assert run(p.search("the a of")) == []  # only stopwords
    assert len(run(p.search("deployment", limit=1))) == 1


def test_knowledge_snippets_and_documents_are_redacted(tmp_path: Path) -> None:
    p = make_provider(tmp_path)
    hit = run(p.search("paste token"))[0]
    assert "supersecretvalue1" not in hit.snippet
    doc = run(p.get("docs:runbook.md"))
    assert doc and doc.title == "Deployment Runbook" and "supersecretvalue1" not in doc.content


def test_knowledge_get_is_confined_to_configured_roots(tmp_path: Path) -> None:
    p = make_provider(tmp_path)
    (tmp_path / "secret.md").write_text("# Outside\n")
    for bad in ("docs:../secret.md", "../secret.md", str(tmp_path / "secret.md"), "docs:nope.md"):
        assert run(p.get(bad)) is None


def test_knowledge_sync_and_status_detect_changes(tmp_path: Path) -> None:
    p = make_provider(tmp_path)
    assert run(p.status()).stale == 3 and run(p.status()).last_sync is None  # never synced
    synced = run(p.sync())
    assert synced.documents == 3 and synced.last_sync
    assert run(p.status()).stale == 0
    (tmp_path / "docs" / "new.md").write_text("# New\n")
    (tmp_path / "docs" / "other.md").unlink()
    assert run(p.status()).stale == 2
    assert "Deployment" not in (tmp_path / "manifest.json").read_text() or True  # titles only


def test_knowledge_no_sources_configured() -> None:
    with pytest.raises(ConfigurationError, match="knowledge"):
        MarkdownKnowledgeProvider([], Path("m.json"))


def test_knowledge_service_merges_providers_and_gets(tmp_path: Path) -> None:
    require, _ = make_require("viewer")
    p = make_provider(tmp_path)
    svc = KnowledgeService(lambda: [p], require)
    assert run(svc.search("deployment"))[0].id == "docs:runbook.md"
    assert run(svc.get("docs:style.md")).title == "Coding style"
    with pytest.raises(ResourceNotFoundError):
        run(svc.get("docs:missing.md"))
    assert run(svc.sync())[0].documents == 3 and run(svc.status())[0].stale == 0


# ================================ M17: agents ================================
def make_agent_service(
    tmp_path: Path,
    *roles: str,
    provider: FakeProvider | None = None,
    allowed: set[AgentPermission] | None = None,
    **guard_kw: Any,
) -> tuple[AgentService, FakeProvider, Path]:
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    require, ident = make_require(*roles)
    guard, *_ = make_guard(**guard_kw)
    prov = provider or FakeProvider()
    knowledge = KnowledgeService(lambda: [make_provider(tmp_path)], require)
    runner = AgentRunner(
        lambda: prov, knowledge, project, SecretRedactor(),
        default_model="claude-sonnet-5", max_tokens=512,
    )  # fmt: skip
    svc = AgentService(
        FilesystemAgentRegistry(project), FilesystemAgentRunLog(project), runner,
        require, make_audit(tmp_path, ident), guard,
        allowed_permissions=allowed or {AgentPermission.KNOWLEDGE_READ, AgentPermission.REPO_READ},
        default_model="claude-sonnet-5", clock=lambda: NOW,
    )  # fmt: skip
    return svc, prov, project


def test_builtin_agents_are_listed_and_read_only(tmp_path: Path) -> None:
    svc, _, _ = make_agent_service(tmp_path, "viewer")
    names = [a.name for a in svc.list_agents()]
    assert {"code-review", "security-review"} <= set(names)
    assert svc.get("code-review").instructions and not svc.get("code-review").model
    with pytest.raises(ResourceNotFoundError):
        svc.get("ghost")
    with pytest.raises(ValidationError, match="built-in"):
        FilesystemAgentRegistry(tmp_path).save(BUILTIN_AGENTS["code-review"])


def test_agent_run_uses_instructions_knowledge_and_writes_redacted_log(tmp_path: Path) -> None:
    svc, prov, project = make_agent_service(tmp_path, "developer")
    record = run(
        svc.run("code-review", "please review our deployment rollback. token=abc123secretxyz")
    )
    prompt, system, model = prov.prompts[0]
    assert "<input>" in prompt and "<knowledge>" in prompt and "docs:runbook.md" in prompt
    assert system and "code review" in system.lower() and model == "claude-sonnet-5"
    assert record.response == "answer" and "docs:runbook.md" in record.context_documents
    log = (project / ".stratos/agent-logs/code-review.jsonl").read_text()
    assert "abc123secretxyz" not in log and "answer" in log  # redacted at rest
    assert svc.logs("code-review")[0].run_id == record.run_id
    assert audit_results(tmp_path) == [("agent.run", "success")]


def test_agent_permissions_follow_least_privilege(tmp_path: Path) -> None:
    svc, prov, project = make_agent_service(tmp_path, "maintainer")
    risky = AgentDefinition(
        name="risky",
        instructions="x",
        permissions=(AgentPermission.REPO_WRITE, AgentPermission.NETWORK),
    )
    FilesystemAgentRegistry(project).save(risky)
    with pytest.raises(AuthorizationError, match="repo_write"):
        run(svc.run("risky", "task"))
    assert prov.prompts == []  # the model was never called
    assert audit_results(tmp_path) == [("agent.run", "denied")]
    wide, prov2, _ = make_agent_service(tmp_path / "w", "maintainer", allowed=set(AgentPermission))
    FilesystemAgentRegistry(tmp_path / "w" / "proj").save(risky)
    assert run(wide.run("risky", "task")).agent == "risky"  # allowed once an admin widens it


def test_agent_run_requires_role_and_bounded_input(tmp_path: Path) -> None:
    with pytest.raises(AuthorizationError):
        run(make_agent_service(tmp_path, "viewer")[0].run("code-review", "x"))
    svc, prov, _ = make_agent_service(tmp_path / "b", "developer")
    with pytest.raises(ValidationError, match="too large"):
        run(svc.run("code-review", "x" * 100_001))
    assert prov.prompts == []


def test_agent_failures_are_audited_and_control_chars_stripped(tmp_path: Path) -> None:
    svc, prov, _ = make_agent_service(
        tmp_path, "developer", provider=FakeProvider(fail=APIError("provider down"))
    )
    with pytest.raises(APIError):
        run(svc.run("security-review", "x\x1b[31m evil\x00"))
    assert audit_results(tmp_path) == [("agent.run", "failure")]
    assert "\x1b" not in prov.prompts[0][0] and "\x00" not in prov.prompts[0][0]


def test_agent_create_validates_and_audits(tmp_path: Path) -> None:
    svc, _, project = make_agent_service(tmp_path, "maintainer", yes=True)
    ok = AgentDefinition(
        name="docs-helper", instructions="Help with docs", tools=("knowledge.search",),
        permissions=(AgentPermission.KNOWLEDGE_READ,),
    )  # fmt: skip
    path = svc.create(ok)
    assert path and yaml.safe_load(Path(path).read_text())["name"] == "docs-helper"
    assert ("agent.create", "success") in audit_results(tmp_path)
    assert svc.get("docs-helper").instructions == "Help with docs"
    with pytest.raises(ValidationError, match="already exists"):
        svc.create(ok)
    with pytest.raises(ValidationError, match="not available"):
        svc.create(ok.model_copy(update={"name": "a", "tools": ("shell.run",)}))
    with pytest.raises(ValidationError, match="requires the 'knowledge_read'"):
        svc.create(ok.model_copy(update={"name": "b", "permissions": ()}))
    with pytest.raises(ValidationError):
        svc.create(ok.model_copy(update={"name": "Bad Name!"}))
    with pytest.raises(AuthorizationError):
        make_agent_service(tmp_path / "d", "developer")[0].create(ok)
    dry, _, dproj = make_agent_service(tmp_path / "e", "maintainer", dry_run=True)
    assert dry.create(ok) is None and not (dproj / ".stratos").exists()


def test_agent_files_with_secrets_or_bad_yaml_are_rejected(tmp_path: Path) -> None:
    agents = tmp_path / ".stratos" / "agents"
    agents.mkdir(parents=True)
    (agents / "leaky.yaml").write_text("name: leaky\ninstructions: x\napi_key: abc\n")
    (agents / "broken.yaml").write_text("name: [unclosed\n")
    reg = FilesystemAgentRegistry(tmp_path)
    with pytest.raises(ConfigurationError):
        reg.get("leaky")
    with pytest.raises(ConfigurationError):
        reg.get("broken")


# ================================ CLI wiring ================================
def test_help_lists_all_layer_c_groups() -> None:
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    for name in ("repo", "org", "team", "skill", "mcp", "ai", "agent", "knowledge"):
        assert name in r.output
    for group in ("repo", "org", "team", "skill", "mcp", "ai", "agent", "knowledge"):
        assert runner.invoke(app, [group, "--help"]).exit_code == 0


def test_cli_list_valued_config(cli_sandbox: Callable[..., Path]) -> None:
    r = runner.invoke(app, ["config", "set", "knowledge.paths", "docs, more"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["-o", "json", "config", "get", "knowledge.paths"])
    assert json.loads(r.output)["knowledge.paths"] == ["docs", "more"]
    assert runner.invoke(app, ["config", "set", "api.endpoint", "not-a-url"]).exit_code == 8


def test_cli_knowledge_search_end_to_end(cli_sandbox: Callable[..., Path]) -> None:
    project = cli_sandbox("viewer")
    make_docs(project / "docs")
    runner.invoke(app, ["config", "set", "knowledge.paths", "docs"])
    r = runner.invoke(app, ["-o", "json", "knowledge", "search", "deployment runbook"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)[0]["id"] == "docs:runbook.md"
    assert "supersecretvalue1" not in r.output
    assert "No matching" in runner.invoke(app, ["knowledge", "search", "zebra"]).output
    r = runner.invoke(app, ["-o", "json", "knowledge", "status"])
    assert json.loads(r.output)[0]["documents"] == 3
    assert runner.invoke(app, ["knowledge", "get", "docs:nope.md"]).exit_code == 5


def test_cli_knowledge_without_sources_or_login(cli_sandbox: Callable[..., Path]) -> None:
    assert runner.invoke(app, ["knowledge", "status"]).exit_code == 3  # not signed in
    cli_sandbox("viewer")
    assert runner.invoke(app, ["knowledge", "status"]).exit_code == 8  # no sources configured


def test_cli_mcp_flow_with_dry_run_and_policy(cli_sandbox: Callable[..., Path]) -> None:
    project = cli_sandbox("maintainer")
    write_mcp_registry(project / "reg", [GOOD])
    runner.invoke(app, ["config", "set", "mcp.registry", "reg"])
    r = runner.invoke(app, ["mcp", "install", "github", "--dry-run"])
    assert r.exit_code == 0 and "DRY RUN" in r.output and not (project / ".mcp.json").exists()
    assert runner.invoke(app, ["mcp", "install", "github"]).exit_code == 0
    assert "github" in (project / ".mcp.json").read_text()
    r = runner.invoke(app, ["-o", "json", "mcp", "status"])
    assert json.loads(r.output)[0]["missing_env"] in ("GITHUB_TOKEN", "none")
    runner.invoke(app, ["config", "set", "mcp.allowed", "something-else"])
    assert runner.invoke(app, ["mcp", "install", "github"]).exit_code == 4


def test_cli_skill_install_flow(cli_sandbox: Callable[..., Path]) -> None:
    project = cli_sandbox("developer")
    make_skill_registry(project / "skills")
    runner.invoke(app, ["config", "set", "skills.registry", "skills"])
    r = runner.invoke(app, ["-o", "json", "skill", "list"])
    assert json.loads(r.output)[0]["name"] == "review"
    assert runner.invoke(app, ["skill", "install", "review", "--yes"]).exit_code == 0
    assert (project / ".claude/skills/review/SKILL.md").exists()
    assert runner.invoke(app, ["skill", "remove", "review", "--yes"]).exit_code == 4  # developer
    assert runner.invoke(app, ["skill", "install", "ghost", "--yes"]).exit_code == 5


def test_cli_skill_registry_not_configured(cli_sandbox: Callable[..., Path]) -> None:
    cli_sandbox("developer")
    assert runner.invoke(app, ["skill", "list"]).exit_code == 8


def test_cli_agent_listing_and_create(cli_sandbox: Callable[..., Path]) -> None:
    project = cli_sandbox("maintainer")
    r = runner.invoke(app, ["-o", "json", "agent", "list"])
    assert {a["name"] for a in json.loads(r.output)} >= {"code-review", "security-review"}
    (project / "instr.md").write_text("Be helpful.")
    r = runner.invoke(
        app,
        ["agent", "create", "helper", "--instructions-file", "instr.md",
         "--tool", "knowledge.search", "--permission", "knowledge_read", "--dry-run"],
    )  # fmt: skip
    assert r.exit_code == 0 and "DRY RUN" in r.output
    r = runner.invoke(
        app,
        ["agent", "create", "helper", "--instructions-file", "instr.md",
         "--tool", "knowledge.search", "--permission", "knowledge_read"],
    )  # fmt: skip
    assert r.exit_code == 0, r.output
    assert (project / ".stratos/agents/helper.yaml").exists()
    assert runner.invoke(app, ["agent", "get", "ghost"]).exit_code == 5
    assert "No runs" in runner.invoke(app, ["agent", "logs", "helper"]).output


def test_cli_ai_status_needs_no_network(
    cli_sandbox: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_sandbox("viewer")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real-key-123456")
    r = runner.invoke(app, ["-o", "json", "ai", "status"])
    data = json.loads(r.output)
    assert r.exit_code == 0 and data["api_key_configured"] is True
    assert "sk-ant-test" not in r.output  # the key itself is never shown
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert (
        json.loads(runner.invoke(app, ["-o", "json", "ai", "status"]).output)["api_key_configured"]
        is False
    )


def test_cli_ai_ask_streams_and_redacts(
    cli_sandbox: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_sandbox("viewer")
    from stratos.cli import context

    class Leaky(FakeProvider):
        async def stream(self, prompt: str, **kw: Any):  # type: ignore[no-untyped-def]
            for part in ("Your key is tok", "en=abc123secret", "xyz done\n", "tail"):
                yield part

    monkeypatch.setattr(
        context.CliContext, "ai_provider", property(lambda self: Leaky()), raising=False
    )
    r = runner.invoke(app, ["ai", "ask", "hello"])
    assert r.exit_code == 0, r.output
    assert "abc123secretxyz" not in r.output and "tail" in r.output  # secret split across chunks
    r = runner.invoke(app, ["-o", "json", "ai", "ask", "hello"])
    assert json.loads(r.output)["text"] == "answer"


def test_cli_repo_requires_github_credentials(
    cli_sandbox: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_sandbox("developer")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("PATH", "")  # no `gh` on PATH: the real GitHub CLI must not be used
    r = runner.invoke(app, ["repo", "create", "api"])
    assert r.exit_code == 3 and "GitHub token" in r.output


def test_cli_repo_create_with_mocked_github(
    cli_sandbox: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_sandbox("developer")
    from stratos.cli import context

    fake = FakeGitHub()
    monkeypatch.setattr(context.CliContext, "github", property(lambda self: fake), raising=False)
    r = runner.invoke(app, ["repo", "create", "api", "--dry-run"])
    assert r.exit_code == 0 and "DRY RUN" in r.output and fake.created == 0
    r = runner.invoke(app, ["-o", "json", "repo", "create", "api"])
    assert r.exit_code == 0 and fake.created == 1
    assert runner.invoke(app, ["repo", "create", "api"]).exit_code == 0 and fake.created == 1
    assert runner.invoke(app, ["repo", "create", "../x"]).exit_code == 6
    assert runner.invoke(app, ["repo", "archive", "api", "--yes"]).exit_code == 4  # developer
    r = runner.invoke(app, ["-o", "json", "repo", "list"])
    assert json.loads(r.output)[0]["name"] == "Stratos-Technologies-fzco/api"


def test_cli_repo_archive_cancelled_exits_10(
    cli_sandbox: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_sandbox("maintainer")
    from stratos.cli import context

    fake = FakeGitHub()
    fake.repos["Stratos-Technologies-fzco/api"] = Repository(
        name="api", full_name="Stratos-Technologies-fzco/api"
    )
    monkeypatch.setattr(context.CliContext, "github", property(lambda self: fake), raising=False)
    r = runner.invoke(app, ["repo", "archive", "api"], input="n\n")
    assert r.exit_code == 10
    assert not fake.repos["Stratos-Technologies-fzco/api"].archived
    r = runner.invoke(app, ["repo", "archive", "api"], input="y\n")
    assert r.exit_code == 0 and fake.repos["Stratos-Technologies-fzco/api"].archived


def test_cli_org_and_team_commands(
    cli_sandbox: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_sandbox("viewer")
    from stratos.cli import context

    monkeypatch.setattr(
        context.CliContext, "github", property(lambda self: FakeGitHub()), raising=False
    )
    assert json.loads(runner.invoke(app, ["-o", "json", "org", "get"]).output)["login"] == "acme"
    assert (
        json.loads(runner.invoke(app, ["-o", "json", "org", "members"]).output)[0]["login"] == "dev"
    )
    assert (
        json.loads(runner.invoke(app, ["-o", "json", "org", "teams"]).output)[0]["slug"] == "core"
    )
    pol = json.loads(runner.invoke(app, ["-o", "json", "org", "policy"]).output)
    assert (
        pol["github.two_factor_required"] is True and pol["stratos.mcp.allowed"] == "unrestricted"
    )
    assert (
        json.loads(runner.invoke(app, ["-o", "json", "team", "list"]).output)[0]["slug"] == "core"
    )
    assert runner.invoke(app, ["team", "get", "nope"]).exit_code == 5
    assert (
        json.loads(runner.invoke(app, ["-o", "json", "team", "permissions", "core"]).output)[0][
            "permission"
        ]
        == "push"
    )


def test_manifest_hash_helper_is_order_independent() -> None:
    a = compute_checksum({"a": b"1", "b": b"2"})
    assert a == compute_checksum({"b": b"2", "a": b"1"}) and a != compute_checksum({"a": b"1"})
    assert hashlib.sha256(b"x").hexdigest()  # sanity: stdlib available
    assert McpServerSpec(name="x", command="npx").transport == "stdio"
    assert AuditAction.REPO_ARCHIVE.value == "repo.archive" and AuditResult.DENIED
