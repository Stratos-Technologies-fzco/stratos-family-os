"""Layer D: workspaces (M21), environments and deployments (M22), diagnostics (M23), CLI flows."""

import base64
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from devfakes import SHA_MAIN, SHA_V1, DevFake, git_clone_fake
from test_layer_c import BASE, NOW, audit_results, gh, make_audit, make_guard, make_require
from typer.testing import CliRunner

from stratos.application.deployment_service import (
    DeploymentService,
    EnvironmentService,
    environment_for,
)
from stratos.application.diagnostics import Facts, run_checks, summarise
from stratos.application.init_service import InitService
from stratos.application.workspace_service import WorkspaceService, venv_python
from stratos.cli.app import app
from stratos.domain.exceptions import (
    AuthorizationError,
    ConfigurationError,
    DependencyError,
    OperationCancelledError,
    ResourceNotFoundError,
    ValidationError,
)
from stratos.domain.models.workflow import ProjectRecord, WorkspaceRecord
from stratos.infrastructure.api.probes import http_probe, tcp_probe
from stratos.infrastructure.filesystem.registry_store import LocalProjectStore, LocalWorkspaceStore
from stratos.infrastructure.filesystem.templates import HOOK_MARKER, PRE_COMMIT_HOOK

runner = CliRunner()
ORG = "acme"
HAS_GIT = shutil.which("git") is not None


def first_json(output: str) -> Any:
    """The first JSON value in mixed command output (data followed by status lines)."""
    return json.JSONDecoder().raw_decode(
        output[output.index(next(c for c in output if c in "[{")) :]
    )[0]


# ================================ M21: workspaces ==============================================
class FakeRunner:
    """Stands in for subprocess: records commands and answers git queries."""

    def __init__(self, remote: str = "https://github.com/acme/api.git", status: str = "") -> None:
        self.calls: list[tuple[list[str], Path]] = []
        self.remote, self.status = remote, status
        self.fail_on: str | None = None

    def __call__(self, cmd: list[str], cwd: Path) -> tuple[int, str]:
        self.calls.append((list(cmd), cwd))
        if self.fail_on and self.fail_on in " ".join(cmd):
            return 1, "boom token=abc123secretxyz"
        if cmd[:4] == ["git", "remote", "get-url", "origin"]:
            return 0, self.remote
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return 0, self.status
        if "venv" in cmd:
            py = venv_python(cwd)
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("")
        return 0, ""

    def ran(self, fragment: str) -> bool:
        return any(fragment in " ".join(c) for c, _ in self.calls)


def fake_clone(cloned: list[Path] | None = None) -> Callable[[str, str, Path], Path]:
    def clone(org: str, name: str, dest: Path) -> Path:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / ".git").mkdir()
        (dest / ".env.example").write_text("API_URL=https://example.test\n")
        (dest / "pyproject.toml").write_text("[project]\nname='x'\n")
        (dest / "requirements.txt").write_text("requests\n")
        (dest / ".github" / "workflows").mkdir(parents=True)
        (dest / ".github" / "workflows" / "ci.yml").write_text("name: CI\n")
        if cloned is not None:
            cloned.append(dest)
        return dest

    return clone


def make_workspace_service(
    tmp_path: Path,
    *roles: str,
    fake_runner: FakeRunner | None = None,
    clone: Callable[[str, str, Path], Path] | None = None,
    which: Callable[[str], str | None] = lambda n: f"/bin/{n}",
    **guard_kw: Any,
) -> tuple[WorkspaceService, LocalWorkspaceStore, FakeRunner, Any]:
    require, ident = make_require(*(roles or ("developer",)))
    guard, out, err, asked = make_guard(**guard_kw)
    audit = make_audit(tmp_path, ident)
    projects = LocalProjectStore(tmp_path / "projects.json")
    projects.save(ProjectRecord(name="api", org=ORG, repository="acme/api"))
    store = LocalWorkspaceStore(tmp_path / "ws.json")
    the_runner = fake_runner or FakeRunner()
    svc = WorkspaceService(
        store, projects, require, audit, guard, root=tmp_path / "root", default_org=ORG,
        clone=clone or fake_clone(),
        init_factory=lambda path: InitService(path, require, audit, guard, org=ORG, ai_provider="claude", ai_model="m"),
        run=the_runner, which=which, clock=lambda: NOW,
    )  # fmt: skip
    return svc, store, the_runner, (out, err, asked)


def by_name(results: list[Any] | None) -> dict[str, Any]:
    assert results is not None
    return {r.name: r for r in results}


def test_workspace_create_assembles_the_workspace_and_is_repeatable(tmp_path: Path) -> None:
    svc, store, _, _ = make_workspace_service(tmp_path, "developer")
    first = by_name(svc.create("dev1", "api"))
    dest = tmp_path / "root" / "dev1"
    assert first["repository"].status == "done" and (dest / ".git").is_dir()
    assert first["claude-and-standards"].status == "done" and (dest / "CLAUDE.md").exists()
    assert first["environment-variables"].status == "done" and (
        dest / ".env"
    ).read_text().startswith("API_URL")
    assert (
        first["git-hooks"].status == "done"
        and HOOK_MARKER in (dest / ".git/hooks/pre-commit").read_text()
    )
    assert first["ci-cd"].detail == "ci.yml" and first["python"].status == "skipped"
    record = store.get("dev1")
    assert (
        record
        and record.project == "acme/api"
        and record.path == str(dest.resolve())
        and record.status == "ready"
    )
    assert "repository" in record.components and "python" not in record.components
    assert ("workspace.create", "success") in audit_results(tmp_path)
    second = by_name(svc.create("dev1", "api"))
    assert {r.status for r in second.values()} <= {"already", "skipped"}  # nothing is redone
    assert store.get("dev1").created_at == record.created_at  # type: ignore[union-attr]


def test_workspace_never_overwrites_env_hooks_or_foreign_folders(tmp_path: Path) -> None:
    svc, _, _, _ = make_workspace_service(tmp_path, "developer")
    svc.create("dev1", "api")
    dest = tmp_path / "root" / "dev1"
    (dest / ".env").write_text("MY_SECRET_SETTING=keep\n")
    (dest / ".git/hooks/pre-commit").write_text("#!/bin/sh\necho mine\n")  # a developer's own hook
    again = by_name(svc.create("dev1", "api"))
    assert (dest / ".env").read_text() == "MY_SECRET_SETTING=keep\n" and again[
        "environment-variables"
    ].status == "already"
    assert (dest / ".git/hooks/pre-commit").read_text() == "#!/bin/sh\necho mine\n"
    assert again["git-hooks"].status == "skipped" and "left untouched" in again["git-hooks"].detail
    stale = dest / ".git/hooks/pre-commit"
    stale.write_text(f"#!/bin/sh\n{HOOK_MARKER}\n# old version\n")  # ours, but outdated: updated
    assert (
        by_name(svc.create("dev1", "api"))["git-hooks"].status == "done"
        and stale.read_text() == PRE_COMMIT_HOOK
    )
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "notes.txt").write_text("x")
    with pytest.raises(ValidationError, match="not empty"):
        svc.create("dev2", "api", path=other)
    assert (other / "notes.txt").exists() and not (other / ".git").exists()
    (tmp_path / "wrong").mkdir()
    (tmp_path / "wrong" / ".git").mkdir()
    svc_other, _, _, _ = make_workspace_service(
        tmp_path / "o",
        "developer",
        fake_runner=FakeRunner(remote="https://github.com/other/thing.git"),
    )
    svc_other.create("w", "api", path=tmp_path / "o" / "copy") if False else None
    mismatched, _, _, _ = make_workspace_service(
        tmp_path / "m",
        "developer",
        fake_runner=FakeRunner(remote="https://github.com/other/thing.git"),
    )
    (tmp_path / "m" / "clone").mkdir(parents=True)
    (tmp_path / "m" / "clone" / ".git").mkdir()
    with pytest.raises(ValidationError, match="not a clone of acme/api"):
        mismatched.create("w", "api", path=tmp_path / "m" / "clone")


def test_workspace_python_and_node_environments_and_dependency_installs(tmp_path: Path) -> None:
    svc, _, run_, _ = make_workspace_service(tmp_path, "developer")
    result = by_name(svc.create("dev1", "api", python=True, node=True))
    assert result["python"].status == "done" and "created" in result["python"].detail
    assert (
        run_.ran("-m venv .venv") and not run_.ran("pip install") and not run_.ran("npm")
    )  # nothing installed
    assert result["node"].detail == "Node.js available"
    svc2, _, run2, _ = make_workspace_service(tmp_path / "b", "developer")
    (tmp_path / "b").mkdir(exist_ok=True)
    r2 = by_name(svc2.create("dev1", "api", python=True, install_deps=True))
    assert (
        run2.ran("pip install -r requirements.txt")
        and "requirements installed" in r2["python"].detail
    )
    again = by_name(svc2.create("dev1", "api", python=True))
    assert again["python"].status == "already"


def test_workspace_node_install_disables_scripts_and_missing_tools_are_reported(
    tmp_path: Path,
) -> None:
    def clone_node(org: str, name: str, dest: Path) -> Path:
        fake_clone()(org, name, dest)
        (dest / "package-lock.json").write_text("{}")
        return dest

    svc, _, run_, _ = make_workspace_service(tmp_path, "developer", clone=clone_node)
    by_name(svc.create("n1", "api", node=True, install_deps=True))
    assert run_.ran("npm ci --ignore-scripts")  # third-party install scripts never run
    missing, _, _, _ = make_workspace_service(tmp_path / "x", "developer", which=lambda n: None)
    with pytest.raises(DependencyError, match="Node.js"):
        missing.create("n2", "api", node=True)


def test_workspace_tool_failures_are_redacted_and_do_not_register_a_workspace(
    tmp_path: Path,
) -> None:
    bad = FakeRunner()
    bad.fail_on = "venv"
    svc, store, _, _ = make_workspace_service(tmp_path, "developer", fake_runner=bad)
    with pytest.raises(DependencyError) as info:
        svc.create("dev1", "api", python=True)
    assert "abc123secretxyz" not in str(info.value) and store.get("dev1") is None
    assert ("workspace.create", "failure") in audit_results(tmp_path)


def test_workspace_create_validation_permissions_and_dry_run(tmp_path: Path) -> None:
    svc, store, _, _ = make_workspace_service(tmp_path, "developer")
    with pytest.raises(ResourceNotFoundError, match="not registered"):
        svc.create("dev1", "ghost")
    with pytest.raises(ValidationError):
        svc.create("../evil", "api")
    svc.create("dev1", "api")
    store2 = LocalProjectStore(tmp_path / "projects.json")
    store2.save(ProjectRecord(name="web", org=ORG, repository="acme/web"))
    with pytest.raises(ValidationError, match="already belongs to project acme/api"):
        svc.create("dev1", "web")
    with pytest.raises(AuthorizationError):
        make_workspace_service(tmp_path / "v", "viewer")[0].create("x", "api")
    dry, dstore, drun, (out, err, _) = make_workspace_service(
        tmp_path / "d", "developer", dry_run=True
    )
    assert dry.create("dev1", "api", python=True) is None
    assert not (tmp_path / "d" / "root").exists() and dstore.list() == [] and not drun.calls
    assert "DRY RUN" in out.export_text() + err.export_text()


def test_workspace_connect_describes_adopts_and_refreshes(tmp_path: Path) -> None:
    svc, store, _, _ = make_workspace_service(tmp_path, "developer")
    svc.create("dev1", "api", python=True)
    info = svc.connect("dev1")
    assert (
        info.problems == [] and info.enter == f'cd "{tmp_path / "root" / "dev1"}"' and info.activate
    )
    with pytest.raises(ResourceNotFoundError) as missing:
        svc.connect("unknown")
    assert "--path" in (missing.value.hint or "")
    (tmp_path / "root" / "dev1" / "CLAUDE.md").unlink()
    refreshed = svc.connect("dev1", refresh=True)
    assert refreshed.problems == [] and (tmp_path / "root" / "dev1" / "CLAUDE.md").exists()
    shutil.rmtree(tmp_path / "root" / "dev1")
    assert any("does not exist" in p for p in svc.connect("dev1").problems)
    existing = tmp_path / "already-cloned"
    (existing / ".git").mkdir(parents=True)
    adopted = svc.connect("mine", path=existing)
    assert adopted.record.project == "acme/api" and store.get("mine") is not None
    with pytest.raises(ValidationError, match="not a git repository"):
        svc.connect("x", path=tmp_path)
    foreign, _, _, _ = make_workspace_service(
        tmp_path / "f", "developer", fake_runner=FakeRunner(remote="https://github.com/o/z.git")
    )
    (tmp_path / "f" / "c" / ".git").mkdir(parents=True)
    with pytest.raises(ValidationError, match="not a clone of any registered project"):
        foreign.connect("z", path=tmp_path / "f" / "c")


def test_workspace_delete_confirms_protects_work_and_audits(tmp_path: Path) -> None:
    svc, store, run_, (_, _, asked) = make_workspace_service(tmp_path, "developer", answer=False)
    svc.create("dev1", "api")
    with pytest.raises(OperationCancelledError):
        svc.delete("dev1")
    assert store.get("dev1") is not None and asked == ["Continue?"]
    yes, ystore, yrun, _ = make_workspace_service(tmp_path / "y", "developer", yes=True)
    yes.create("dev1", "api")
    folder = tmp_path / "y" / "root" / "dev1"
    assert (
        yes.delete("dev1") and ystore.get("dev1") is None and folder.exists()
    )  # folder kept by default
    with pytest.raises(ResourceNotFoundError):
        yes.delete("dev1")
    yes.create("dev1", "api")
    yrun.status = " M src/app.py"
    with pytest.raises(ValidationError, match="uncommitted changes"):
        yes.delete("dev1", purge_files=True)
    assert folder.exists() and ystore.get("dev1") is not None  # nothing was deleted
    assert yes.delete("dev1", purge_files=True, force=True) and not folder.exists()
    assert ("workspace.delete", "success") in audit_results(tmp_path / "y")
    yrun.status = ""
    yes.create("dev2", "api")
    shutil.rmtree(tmp_path / "y" / "root" / "dev2" / ".git")
    with pytest.raises(ValidationError, match="not a project working copy"):
        yes.delete("dev2", purge_files=True)
    dry, dstore, _, _ = make_workspace_service(tmp_path / "z", "developer", dry_run=True)
    dstore.save(WorkspaceRecord(name="w", project="acme/api", path=str(tmp_path)))
    assert dry.delete("w", purge_files=True) is None and dstore.get("w") is not None


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_the_installed_hook_really_blocks_secrets_and_env_files(tmp_path: Path) -> None:
    svc, _, _, _ = make_workspace_service(
        tmp_path,
        "developer",
        clone=git_clone_fake("https://github.com/acme/api.git"),
        fake_runner=None,
    )
    from stratos.application.workspace_service import default_runner

    svc._run = default_runner  # real git for this test
    svc.create("dev1", "api", hooks=True)
    repo = tmp_path / "root" / "dev1"
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x.io",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x.io",
    }

    def commit(files: dict[str, str]) -> subprocess.CompletedProcess[str]:
        for name, text in files.items():
            (repo / name).write_text(text)
            subprocess.run(["git", "-C", str(repo), "add", "-f", name], check=True, env=env)
        return subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "test"],
            capture_output=True,
            text=True,
            env=env,
        )

    blocked = commit({".env": "A=1\n"})
    assert blocked.returncode != 0 and "environment files" in blocked.stderr
    subprocess.run(["git", "-C", str(repo), "reset", "-q"], check=True, env=env)
    key = commit({"deploy.txt": "ghp_" + "a" * 30 + "\n"})
    assert key.returncode != 0 and "secret" in key.stderr
    subprocess.run(["git", "-C", str(repo), "reset", "-q"], check=True, env=env)
    assert commit({"README.md": "hello\n"}).returncode == 0  # ordinary commits are unaffected
    assert commit({".env.example": "A=\n"}).returncode == 0  # example files are allowed


# ================================ M22: environments ==================================================
def make_ops(
    tmp_path: Path, *roles: str, **guard_kw: Any
) -> tuple[EnvironmentService, DeploymentService, DevFake, LocalProjectStore, Any]:
    fake = DevFake()
    require, ident = make_require(*(roles or ("maintainer",)))
    guard, out, err, asked = make_guard(**guard_kw)
    audit = make_audit(tmp_path, ident)
    store = LocalProjectStore(tmp_path / "projects.json")
    store.save(ProjectRecord(name="api", org=ORG, repository="acme/api", default_branch="main"))
    kw = dict(default_org=ORG, default_environment="development")
    envs = EnvironmentService(store, fake, require, audit, guard, **kw)
    deploys = DeploymentService(store, fake, require, audit, guard, **kw)
    return envs, deploys, fake, store, (out, err, asked)


def test_environment_aliases() -> None:
    assert [environment_for(n) for n in ("dev", "staging", "production", "qa")] == [
        "development", "staging", "production", "qa",
    ]  # fmt: skip


def test_environment_create_is_idempotent_protected_and_recorded(tmp_path: Path) -> None:
    envs, _, fake, store, _ = make_ops(tmp_path)
    env, created = envs.create("api", "staging")  # type: ignore[misc]
    assert created and env.protected and fake.envs["staging"]["protected"] is True
    again, created2 = envs.create("api", "staging")  # type: ignore[misc]
    assert not created2 and fake.calls.count("create_environment") == 1
    envs.create("api", "dev")
    assert fake.envs["development"]["protected"] is False  # `dev` is the development environment
    envs.create("api")  # default environment
    assert store.get(ORG, "api").environments == ("staging", "development")  # type: ignore[union-attr]
    assert (
        audit_results(tmp_path).count(("environment.create", "success")) == 2
    )  # only real creations
    assert envs.get("api", "staging").protected and envs.get("api").name == "development"
    assert {e.name for e in envs.list_environments("api")} == {"staging", "development"}
    with pytest.raises(ResourceNotFoundError, match="does not exist"):
        envs.get("api", "qa")
    with pytest.raises(ResourceNotFoundError, match="not registered"):
        envs.list_environments("ghost")
    with pytest.raises(AuthorizationError):
        make_ops(tmp_path / "v", "viewer")[0].create("api", "x")


def test_environment_create_dry_run_and_delete_flow(tmp_path: Path) -> None:
    dry, _, dfake, _, (out, err, _) = make_ops(tmp_path / "d", "maintainer", dry_run=True)
    assert dry.create("api", "staging") is None and dfake.envs == {}
    assert "DRY RUN" in out.export_text() + err.export_text()
    envs, _, fake, store, (out2, err2, asked) = make_ops(tmp_path, "maintainer", answer=False)
    envs.create("api", "production")
    with pytest.raises(OperationCancelledError):
        envs.delete("api", "production")
    assert (
        "production" in fake.envs
        and "PRODUCTION ENVIRONMENT" in out2.export_text() + err2.export_text()
    )
    yes, _, fake2, store2, _ = make_ops(tmp_path / "y", "maintainer", yes=True)
    yes.create("api", "staging")
    assert yes.delete("api", "staging") == "staging" and fake2.envs == {}
    assert store2.get(ORG, "api").environments == ()  # type: ignore[union-attr]
    assert ("environment.delete", "success") in audit_results(tmp_path / "y")
    with pytest.raises(ResourceNotFoundError):
        yes.delete("api", "staging")
    dev, _, _, _, _ = make_ops(tmp_path / "dv", "developer", yes=True)
    with pytest.raises(AuthorizationError):
        dev.delete("api", "staging")


# ================================ M22: deployments ======================================================
def test_deploy_requests_a_deployment_and_never_claims_success(tmp_path: Path) -> None:
    _, deploys, fake, _, _ = make_ops(tmp_path, "developer")
    result = deploys.deploy("api", "dev")
    assert result and result.created
    d = result.deployment
    assert d.environment == "development" and d.sha == SHA_MAIN and d.state == "queued"
    assert "development" in fake.envs  # the environment is created on demand
    assert fake.statuses[d.id][0].state == "queued"  # only "queued": the pipeline reports the rest
    assert (
        d.id == fake.deployments[0].id
        and fake.deployments[0].payload["stratos"]["project"] == "acme/api"
    )  # type: ignore[index]
    assert audit_results(tmp_path) == [("deployment.create", "success")]
    events = json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[-1])
    assert (
        events["details"]["sha"] == SHA_MAIN and events["details"]["environment"] == "development"
    )


def test_deploy_per_target_ref_and_duplicate_detection(tmp_path: Path) -> None:
    _, deploys, fake, _, _ = make_ops(tmp_path, "developer")
    staging = deploys.deploy("api", "staging", ref="v1")
    assert (
        staging and staging.deployment.sha == SHA_V1 and fake.envs["staging"]["protected"] is True
    )
    dup = deploys.deploy("api", "staging", ref="v1")
    assert (
        dup and not dup.created and dup.deployment.id == staging.deployment.id
    )  # identical request in flight
    assert len(fake.deployments) == 1
    fake.finish(staging.deployment.id, "success")
    again = deploys.deploy("api", "staging", ref="v1")  # finished: a new deployment is allowed
    assert again and again.created and len(fake.deployments) == 2
    other_env = deploys.deploy("api", "dev", ref="v1")
    assert other_env and other_env.created  # a different environment is independent
    with pytest.raises(ResourceNotFoundError):
        deploys.deploy("api", "dev", ref="no-such-ref")
    with pytest.raises(ResourceNotFoundError, match="not registered"):
        deploys.deploy("ghost", "dev")
    with pytest.raises(AuthorizationError):
        make_ops(tmp_path / "v", "viewer")[1].deploy("api", "dev")


def test_deploy_dry_run_and_production_confirmation(tmp_path: Path) -> None:
    _, dry, fake, _, (out, err, _) = make_ops(tmp_path / "d", "developer", dry_run=True)
    assert dry.deploy("api", "production") is None and fake.deployments == [] and fake.envs == {}
    assert (
        "DRY RUN" in out.export_text() + err.export_text() and audit_results(tmp_path / "d") == []
    )
    _, deploys, fake2, _, (_, _, asked) = make_ops(tmp_path, "developer", answer=False)
    with pytest.raises(OperationCancelledError):
        deploys.deploy("api", "production")
    assert asked == ["Continue?"] and fake2.deployments == [] and audit_results(tmp_path) == []
    deploys.deploy("api", "dev")  # non-production targets do not ask
    assert asked == ["Continue?"] and len(fake2.deployments) == 1
    _, approved, fake3, _, _ = make_ops(tmp_path / "y", "developer", yes=True)
    assert (
        approved.deploy("api", "production").created
        and fake3.envs["production"]["protected"] is True
    )  # type: ignore[union-attr]


def test_deploy_is_not_retried_after_a_failure_and_is_audited(tmp_path: Path) -> None:
    _, deploys, fake, _, _ = make_ops(tmp_path, "developer")
    fake.fail["create_deployment"] = 1
    from stratos.domain.exceptions import APIError

    with pytest.raises(APIError):
        deploys.deploy("api", "dev")
    assert (
        fake.calls.count("create_deployment") == 1 and fake.deployments == []
    )  # exactly one attempt
    assert audit_results(tmp_path) == [("deployment.create", "failure")]


def test_deployment_status_reports_the_pipeline_state(tmp_path: Path) -> None:
    _, deploys, fake, _, _ = make_ops(tmp_path, "developer")
    assert deploys.status("api") == []
    d1 = deploys.deploy("api", "dev").deployment  # type: ignore[union-attr]
    s = deploys.status("api", "dev")
    assert [x.state for x in s] == ["queued"]
    fake.finish(d1.id, "in_progress")
    fake.finish(d1.id, "success")
    fake.envs["staging"] = {"protected": True}
    deploys.deploy("api", "staging", ref="v1")
    everything = {x.environment: x.state for x in deploys.status("api")}
    assert everything == {"development": "success", "staging": "queued"}
    assert [x.environment for x in deploys.status("api", "staging")] == ["staging"]
    fake.statuses[fake.deployments[0].id] = []
    assert deploys.status("api", "staging")[0].state == "pending"  # no status posted yet


def _ship(deploys: DeploymentService, fake: DevFake, ref: str, state: str) -> int:
    dep = deploys.deploy("api", "production", ref=ref).deployment  # type: ignore[union-attr]
    fake.finish(dep.id, state)
    return dep.id


def test_rollback_returns_to_the_previous_successful_version(tmp_path: Path) -> None:
    _, deploys, fake, _, _ = make_ops(tmp_path, "maintainer", yes=True)
    good = _ship(deploys, fake, "v1", "success")
    _ship(deploys, fake, "v2", "failure")  # a failed release must never be a rollback target
    current = _ship(deploys, fake, "main", "success")
    created = deploys.rollback("api", "production")
    assert created and created.sha == SHA_V1 and created.state == "queued"
    dep = fake.deployments[0]
    assert dep.payload["stratos"]["rollback_of"] == current and dep.description.startswith(
        "Rollback"
    )  # type: ignore[index]
    assert good != dep.id and fake.statuses[dep.id][0].state == "queued"
    assert ("deployment.rollback", "success") in audit_results(tmp_path)


def test_rollback_explicit_target_errors_confirmation_and_roles(tmp_path: Path) -> None:
    _, deploys, fake, _, _ = make_ops(tmp_path, "maintainer", yes=True)
    only = _ship(deploys, fake, "main", "success")
    with pytest.raises(ValidationError, match="no earlier successful deployment"):
        deploys.rollback("api", "production")
    with pytest.raises(ResourceNotFoundError, match="no deployments"):
        deploys.rollback("api", "staging")
    with pytest.raises(ResourceNotFoundError, match="999"):
        deploys.rollback("api", "production", to=999)
    second = _ship(deploys, fake, "v2", "success")
    picked = deploys.rollback("api", "production", to=only)
    assert picked and picked.sha == SHA_MAIN and second != only
    _, asking, fake2, _, (_, _, asked) = make_ops(tmp_path / "c", "maintainer", answer=False)
    _ship_no_confirm = DeploymentService.__new__(DeploymentService)
    _ship_no_confirm.__dict__.update(asking.__dict__)
    _ship_no_confirm._guard = make_guard(yes=True)[0]
    _ship(_ship_no_confirm, fake2, "v1", "success")
    _ship(_ship_no_confirm, fake2, "main", "success")
    before = len(fake2.deployments)
    with pytest.raises(OperationCancelledError):
        asking.rollback("api", "production")
    assert len(fake2.deployments) == before  # cancelled: no new deployment
    dev, dfake = make_ops(tmp_path / "d", "developer")[1:3]
    with pytest.raises(AuthorizationError):
        dev.rollback("api", "production")
    _, dry, dfake2, _, (out, err, _) = make_ops(tmp_path / "e", "maintainer", dry_run=True)
    dfake2.deployments = list(fake.deployments)
    dfake2.statuses = dict(fake.statuses)
    n = len(dfake2.deployments)
    assert dry.rollback("api", "production") is None and len(dfake2.deployments) == n
    assert "DRY RUN" in out.export_text() + err.export_text()


# ================================ GitHub adapter: files, environments, deployments =====================
def content(text: bytes, sha: str = "abc") -> dict[str, Any]:
    return {"type": "file", "content": base64.b64encode(text).decode(), "sha": sha}


def test_github_files_get_put_and_safety() -> None:
    puts: list[dict[str, Any]] = []
    state: dict[str, Any] = {"data": None}

    def get(req: httpx.Request) -> httpx.Response:
        if state["data"] is None:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json=content(state["data"]))

    def put(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        puts.append(body)
        state["data"] = base64.b64decode(body["content"])
        return httpx.Response(201, json={})

    path = "/repos/acme/api/contents/docs/a b.md"
    svc = gh({("GET", path): get, ("PUT", path): put})
    assert svc.get_file("acme", "api", "docs/a b.md") is None
    assert svc.put_file("acme", "api", "docs/a b.md", "hello", branch="main", message="m") is True
    assert "sha" not in puts[0] and puts[0]["branch"] == "main" and puts[0]["message"] == "m"
    assert svc.get_file("acme", "api", "docs/a b.md") == "hello"
    assert (
        svc.put_file("acme", "api", "docs/a b.md", "hello", branch="main", message="m") is False
    )  # identical
    assert (
        svc.put_file("acme", "api", "docs/a b.md", b"\x00\x01binary", branch="main", message="m")
        is True
    )
    assert puts[1]["sha"] == "abc" and len(puts) == 2
    for bad in ("../x", "/abs", "a/../b", ""):
        with pytest.raises(ValidationError):
            svc.put_file("acme", "api", bad, "x", branch="main", message="m")
    folder = gh(
        {("GET", "/repos/acme/api/contents/src"): httpx.Response(200, json={"type": "dir"})}
    )
    with pytest.raises(ValidationError, match="not a file"):
        folder.get_file("acme", "api", "src")


def test_github_update_repo_and_resolve_ref() -> None:
    bodies: list[Any] = []

    def patch(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(
            200, json={"name": "api", "full_name": "acme/api", "description": "d"}
        )

    svc = gh(
        {
            ("PATCH", "/repos/acme/api"): patch,
            ("GET", "/repos/acme/api/commits/main"): httpx.Response(200, json={"sha": SHA_MAIN}),
        }
    )
    assert svc.update_repo("acme", "api", description="d").description == "d" and bodies == [
        {"description": "d"}
    ]
    assert svc.resolve_ref("acme", "api", "main") == SHA_MAIN
    for bad in ("../x", "-flag", "has space", ""):
        with pytest.raises(ValidationError):
            svc.resolve_ref("acme", "api", bad)


def test_github_environments_create_protect_fallback_list_get_delete() -> None:
    seen: list[dict[str, Any]] = []

    def put(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        if body.get("deployment_branch_policy") and req.url.path.endswith("/locked"):
            return httpx.Response(422, json={"message": "protection rules need a paid plan"})
        return httpx.Response(200, json={"name": req.url.path.rsplit("/", 1)[1], "html_url": "u", "created_at": "t",
                                         "deployment_branch_policy": body.get("deployment_branch_policy")})  # fmt: skip

    svc = gh(
        {
            ("PUT", "/repos/acme/api/environments/staging"): put,
            ("PUT", "/repos/acme/api/environments/locked"): put,
            ("PUT", "/repos/acme/api/environments/development"): put,
            ("GET", "/repos/acme/api/environments"): httpx.Response(
                200, json={"environments": [{"name": "staging", "html_url": "u"}]}
            ),
            ("GET", "/repos/acme/api/environments/staging"): httpx.Response(
                200, json={"name": "staging", "protection_rules": [{"type": "x"}]}
            ),
            ("DELETE", "/repos/acme/api/environments/staging"): httpx.Response(204),
        }
    )
    protected = svc.create_environment("acme", "api", "staging", protected=True)
    assert protected.protected and seen[0] == {
        "deployment_branch_policy": {"protected_branches": True, "custom_branch_policies": False}
    }
    plain = svc.create_environment("acme", "api", "development")
    assert not plain.protected and seen[1] == {}
    fallback = svc.create_environment(
        "acme", "api", "locked", protected=True
    )  # 422 -> created unprotected
    assert not fallback.protected and seen[-1] == {}
    assert [e.name for e in svc.list_environments("acme", "api")] == ["staging"]
    assert svc.get_environment("acme", "api", "staging").protected  # type: ignore[union-attr]
    assert svc.get_environment("acme", "api", "nope") is None
    svc.delete_environment("acme", "api", "staging")
    for bad in ("a/b", "x y", "", "a" * 60):
        with pytest.raises(ValidationError):
            svc.create_environment("acme", "api", bad)


def test_github_deployments_create_list_statuses() -> None:
    bodies: dict[str, Any] = {}

    def create(req: httpx.Request) -> httpx.Response:
        bodies["create"] = json.loads(req.content)
        if bodies["create"]["ref"] == "merge-needed":
            return httpx.Response(202, json={"message": "Auto-merged the default branch"})
        return httpx.Response(201, json={"id": 5, "ref": bodies["create"]["ref"], "sha": SHA_MAIN, "environment": "production",
                                         "creator": {"login": "dev"}, "created_at": "t", "payload": bodies["create"]["payload"]})  # fmt: skip

    def listing(req: httpx.Request) -> httpx.Response:
        bodies["query"] = dict(req.url.params)
        return httpx.Response(
            200, json=[{"id": 6, "ref": "main", "sha": SHA_V1, "environment": "production"}]
        )

    def add(req: httpx.Request) -> httpx.Response:
        bodies["status"] = json.loads(req.content)
        return httpx.Response(201, json={})

    svc = gh(
        {
            ("POST", "/repos/acme/api/deployments"): create,
            ("GET", "/repos/acme/api/deployments"): listing,
            ("GET", "/repos/acme/api/deployments/5/statuses"): httpx.Response(
                200,
                json=[
                    {"state": "success", "description": "ok", "created_at": "t"},
                    {"state": "queued"},
                ],
            ),
            ("POST", "/repos/acme/api/deployments/5/statuses"): add,
        }
    )
    dep = svc.create_deployment(
        "acme",
        "api",
        ref=SHA_MAIN,
        environment="production",
        description="x" * 300,
        payload={"a": 1},
    )
    b = bodies["create"]
    assert dep.id == 5 and dep.creator == "dev" and dep.payload == {"a": 1}
    assert (
        b["auto_merge"] is False
        and b["required_contexts"] == []
        and b["production_environment"] is True
    )
    assert len(b["description"]) == 140 and b["environment"] == "production"
    with pytest.raises(ValidationError, match="Auto-merged"):
        svc.create_deployment("acme", "api", ref="merge-needed", environment="production")
    listed = svc.list_deployments("acme", "api", environment="production", limit=1)
    assert listed[0].id == 6 and bodies["query"]["environment"] == "production"
    assert [s.state for s in svc.deployment_statuses("acme", "api", 5)] == ["success", "queued"]
    svc.add_deployment_status("acme", "api", 5, "queued", description="d" * 200)
    assert bodies["status"]["state"] == "queued" and len(bodies["status"]["description"]) == 140
    with pytest.raises(ValidationError, match="Invalid deployment state"):
        svc.add_deployment_status("acme", "api", 5, "done-ish")
    with pytest.raises(ValidationError):
        svc.create_deployment("acme", "api", ref="../x", environment="production")
    assert BASE.startswith("https://")


# ================================ M23: diagnostics ===============================================
def facts(**over: Any) -> Facts:
    base: dict[str, Any] = dict(
        python=(3, 12), config_error=None, keyring_backend="windows.WinVaultKeyring", auth_configured=True,
        signed_in=True, git_found=True, github_token_found=True, claude_level="pass",
        claude_detail="Claude Code detected.", ai_provider="claude", ai_key_configured=True,
        skills_registry=True, mcp_registry=True, knowledge_sources=1,
    )  # fmt: skip
    return Facts(**{**base, **over})


def levels(f: Facts) -> dict[str, str]:
    return {c.name: c.level for c in run_checks(f)}


def test_doctor_layer_d_checks_pass_warn_fail() -> None:
    full = facts(
        uv_found=True, docker_found=True, project_manifest="valid",
        credential_env_vars=("GITHUB_TOKEN",), permissions=("org.read", "repo.create"),
        online={"Network (github.com)": (True, "ok"), "GitHub API": (True, "ok"), "Platform API": (True, "ok")},
    )  # fmt: skip
    assert summarise(run_checks(full)) == (20, 0, 0)
    weak = levels(
        facts(
            uv_found=False,
            docker_found=False,
            project_manifest="missing",
            credential_env_vars=(),
            permissions=(),
        )
    )
    assert (
        weak["uv"]
        == weak["Docker"]
        == weak["Project configuration"]
        == weak["Environment variables"]
        == "warn"
    )
    assert weak["Permissions"] == "warn"
    broken = levels(facts(project_manifest="invalid: bad yaml"))
    assert broken["Project configuration"] == "fail"
    offline = levels(
        facts(
            online={
                "Network (github.com)": (False, "unreachable"),
                "Platform API": (False, "unreachable"),
            }
        )
    )
    assert (
        offline["Network (github.com)"] == offline["Platform API"] == "warn"
    )  # connectivity is advice, not a failure
    assert "uv" not in levels(facts())  # checks appear only when their facts were gathered


def test_probes_report_reachability_without_sending_credentials() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(404 if req.url.path == "/missing" else 200)

    transport = httpx.MockTransport(handler)
    assert http_probe("https://api.example.com/ok", transport=transport) == (
        True,
        "reachable (HTTP 200)",
    )
    assert http_probe("https://api.example.com/missing", transport=transport)[0] is False
    assert (
        http_probe("https://api.example.com/missing", transport=transport, any_response=True)[0]
        is True
    )
    assert all("Authorization" not in r.headers for r in seen)

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    ok, detail = http_probe("https://x.example", transport=httpx.MockTransport(boom))
    assert not ok and "ConnectError" in detail
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing listens there now
    reachable, message = tcp_probe("127.0.0.1", port, timeout=1.0)
    assert reachable is False and "unreachable" in message


# ================================ CLI: end-to-end flows =====================================================
@pytest.fixture
def d_env(
    cli_sandbox: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> Callable[..., tuple[Path, DevFake]]:
    from stratos.cli import context

    def setup(*roles: str) -> tuple[Path, DevFake]:
        project = cli_sandbox(*roles)
        fake = DevFake()
        monkeypatch.setattr(
            context.CliContext, "github", property(lambda self: fake), raising=False
        )
        return project, fake

    return setup


def out_of(result: Any) -> str:
    return result.output


def test_cli_project_lifecycle_and_init_manifest_resolution(
    d_env: Callable[..., tuple[Path, DevFake]],
) -> None:
    folder, fake = d_env("maintainer")
    name = folder.name
    r = runner.invoke(app, ["project", "create", name, "--dry-run", "--owner", "@acme/team"])
    assert r.exit_code == 0 and "DRY RUN" in r.output and fake.created == 0
    r = runner.invoke(
        app,
        ["-o", "json", "project", "create", name, "--owner", "@acme/team", "--template", "python"],
    )
    assert r.exit_code == 0, r.output
    steps = first_json(r.output)
    assert [s["result"] for s in steps][:2] == ["done", "done"] and fake.created == 1
    again = runner.invoke(app, ["project", "create", name])
    flat = again.output.replace("\n", " ")
    assert again.exit_code == 0 and "already exists" in " ".join(flat.split()) and fake.created == 1
    rows = first_json(runner.invoke(app, ["-o", "json", "project", "list"]).output)
    assert rows[0]["name"] == name and rows[0]["status"] == "active"
    detail = first_json(runner.invoke(app, ["-o", "json", "project", "get", name]).output)
    assert detail["repository"].endswith(f"/{name}") and "development" in detail["environments"]
    assert runner.invoke(app, ["project", "get", "ghost"]).exit_code == 5
    assert runner.invoke(app, ["project", "update", name, "--description", "Hello"]).exit_code == 0
    assert runner.invoke(app, ["project", "update", name]).exit_code == 6
    assert runner.invoke(app, ["project", "create", "../x"]).exit_code == 6
    # inside a folder set up with `stratos init`, --project can be omitted
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["environment", "list"]).exit_code == 0
    s = first_json(runner.invoke(app, ["-o", "json", "status"]).output)
    assert s["project"] == name and s["registered_projects"] == 1
    r = runner.invoke(app, ["project", "delete", name], input="n\n")
    assert r.exit_code == 10 and first_json(
        runner.invoke(app, ["-o", "json", "project", "list"]).output
    )
    assert runner.invoke(app, ["project", "delete", name, "--yes"]).exit_code == 0
    assert runner.invoke(app, ["project", "delete", name, "--yes"]).exit_code == 5


def test_cli_init_is_repeatable_and_supports_dry_run_and_diff(
    d_env: Callable[..., tuple[Path, DevFake]],
) -> None:
    folder, _ = d_env("developer")
    (folder / "CLAUDE.md").write_text("# Ours\nKeep me.\n")
    dry = runner.invoke(app, ["init", "--dry-run"])
    assert dry.exit_code == 0 and "DRY RUN" in dry.output and "would-change" in dry.output
    assert "+" in dry.output and not (folder / ".stratos" / "project.yaml").exists()
    first = runner.invoke(app, ["init", "--diff"])
    assert first.exit_code == 0 and "changed" in first.output
    assert (folder / ".stratos/project.yaml").exists() and "Keep me." in (
        folder / "CLAUDE.md"
    ).read_text()
    second = runner.invoke(app, ["init"])
    assert second.exit_code == 0 and "nothing to change" in second.output
    via_project = runner.invoke(app, ["project", "init"])
    assert via_project.exit_code == 0 and "nothing to change" in via_project.output
    assert runner.invoke(app, ["init", "--template", "cobol"]).exit_code == 6


def test_cli_environment_and_deploy_flow(d_env: Callable[..., tuple[Path, DevFake]]) -> None:
    folder, fake = d_env("maintainer")
    runner.invoke(app, ["project", "create", "api"])
    assert (
        runner.invoke(app, ["environment", "create", "staging", "--project", "api"]).exit_code == 0
    )
    assert (
        "already exists"
        in runner.invoke(app, ["environment", "create", "staging", "--project", "api"]).output
    )
    envs = first_json(
        runner.invoke(app, ["-o", "json", "environment", "list", "--project", "api"]).output
    )
    assert {e["name"] for e in envs} == {"development", "staging"}
    assert runner.invoke(app, ["environment", "get", "qa", "--project", "api"]).exit_code == 5
    r = runner.invoke(app, ["deploy", "dev", "--project", "api", "--dry-run"])
    assert r.exit_code == 0 and "DRY RUN" in r.output and fake.deployments == []
    r = runner.invoke(app, ["-o", "json", "deploy", "dev", "--project", "api"])
    assert (
        r.exit_code == 0 and first_json(r.output)["state"] == "queued" and "requested" in r.output
    )
    dup = runner.invoke(app, ["deploy", "dev", "--project", "api"])
    assert dup.exit_code == 0 and "already in progress" in dup.output and len(fake.deployments) == 1
    fake.finish(fake.deployments[0].id, "success")
    assert (
        runner.invoke(app, ["deploy", "production", "--project", "api"], input="n\n").exit_code
        == 10
    )
    assert (
        runner.invoke(
            app, ["deploy", "production", "--project", "api", "--ref", "v1", "--yes"]
        ).exit_code
        == 0
    )
    fake.finish(fake.deployments[0].id, "success")
    assert runner.invoke(app, ["deploy", "production", "--project", "api", "--yes"]).exit_code == 0
    fake.finish(fake.deployments[0].id, "success")
    status = first_json(
        runner.invoke(app, ["-o", "json", "deploy", "status", "--project", "api"]).output
    )
    assert {row["environment"]: row["state"] for row in status} == {
        "development": "success",
        "production": "success",
    }
    r = runner.invoke(
        app, ["deploy", "rollback", "-e", "production", "--project", "api"], input="n\n"
    )
    assert r.exit_code == 10
    r = runner.invoke(
        app, ["-o", "json", "deploy", "rollback", "-e", "production", "--project", "api", "--yes"]
    )
    assert (
        r.exit_code == 0
        and first_json(r.output)["sha"] == SHA_V1[:7]
        and "Rollback requested" in r.output
    )
    assert (
        runner.invoke(
            app, ["deploy", "rollback", "-e", "qa", "--project", "api", "--yes"]
        ).exit_code
        == 5
    )
    assert runner.invoke(app, ["deploy", "status", "--project", "ghost"]).exit_code == 5
    assert (
        runner.invoke(
            app, ["environment", "delete", "staging", "--project", "api"], input="n\n"
        ).exit_code
        == 10
    )
    assert (
        runner.invoke(
            app, ["environment", "delete", "staging", "--project", "api", "--yes"]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(app, ["deploy", "status"]).exit_code == 6
    )  # no --project and not a Stratos folder
    assert folder.exists()


def test_cli_deploy_permissions(d_env: Callable[..., tuple[Path, DevFake]]) -> None:
    d_env("maintainer")
    runner.invoke(app, ["project", "create", "api"])
    from stratos.cli.app import app as cli_app  # noqa: F401

    dev_env = d_env("developer")
    assert (
        runner.invoke(
            app, ["environment", "delete", "development", "--project", "api", "--yes"]
        ).exit_code
        == 4
    )
    assert (
        runner.invoke(
            app, ["deploy", "rollback", "-e", "development", "--project", "api", "--yes"]
        ).exit_code
        == 4
    )
    assert dev_env is not None


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_cli_workspace_flow(
    d_env: Callable[..., tuple[Path, DevFake]], monkeypatch: pytest.MonkeyPatch
) -> None:
    folder, fake = d_env("maintainer")
    from stratos.infrastructure.github import git as git_module

    monkeypatch.setattr(
        git_module,
        "clone_repository",
        git_clone_fake("https://github.com/Stratos-Technologies-fzco/api.git"),
    )
    runner.invoke(app, ["project", "create", "api"])
    dest = folder / "ws1"
    dry = runner.invoke(
        app, ["workspace", "create", "w1", "--project", "api", "--path", str(dest), "--dry-run"]
    )
    assert dry.exit_code == 0 and "DRY RUN" in dry.output and not dest.exists()
    made = runner.invoke(
        app, ["workspace", "create", "w1", "--project", "api", "--path", str(dest)]
    )
    assert made.exit_code == 0, made.output
    assert (
        (dest / ".git" / "hooks" / "pre-commit").exists()
        and (dest / ".env").exists()
        and (dest / "CLAUDE.md").exists()
    )
    again = runner.invoke(
        app, ["workspace", "create", "w1", "--project", "api", "--path", str(dest)]
    )
    assert again.exit_code == 0
    assert (
        first_json(runner.invoke(app, ["-o", "json", "workspace", "list"]).output)[0]["name"]
        == "w1"
    )
    assert first_json(runner.invoke(app, ["-o", "json", "workspace", "get", "w1"]).output)[
        "project"
    ].endswith("/api")
    connected = runner.invoke(app, ["workspace", "connect", "w1"])
    assert connected.exit_code == 0 and "cd" in connected.output
    assert runner.invoke(app, ["workspace", "connect", "nope"]).exit_code == 5
    assert runner.invoke(app, ["workspace", "create", "w2", "--project", "ghost"]).exit_code == 5
    assert runner.invoke(app, ["workspace", "delete", "w1"], input="n\n").exit_code == 10
    assert (
        runner.invoke(app, ["workspace", "delete", "w1", "--yes"]).exit_code == 0 and dest.exists()
    )
    assert runner.invoke(app, ["workspace", "delete", "w1", "--yes"]).exit_code == 5
    assert fake is not None


def test_cli_doctor_online_version_and_status(
    d_env: Callable[..., tuple[Path, DevFake]], monkeypatch: pytest.MonkeyPatch
) -> None:
    folder, _ = d_env("viewer")
    monkeypatch.setenv("PATH", "")
    from stratos.infrastructure.api import probes

    monkeypatch.setattr(
        probes, "tcp_probe", lambda host, port, **k: (True, f"{host}:{port} reachable")
    )
    monkeypatch.setattr(
        probes,
        "http_probe",
        lambda url, **k: (
            (False, "unreachable (ConnectError)")
            if "example" in url
            else (True, "reachable (HTTP 200)")
        ),
    )
    r = runner.invoke(app, ["-o", "json", "doctor", "--online"])
    assert r.exit_code == 0, r.output  # unreachable services are warnings, never failures
    rows = {row["check"]: row for row in first_json(r.output)}
    assert (
        rows["Network (github.com)"]["result"] == "PASS" and rows["GitHub API"]["result"] == "PASS"
    )
    assert (
        rows["Platform API"]["result"] == "WARN"
        and rows["Project configuration"]["result"] == "WARN"
    )
    assert rows["Permissions"]["result"] == "PASS" and "org.read" in rows["Permissions"]["detail"]
    offline = {
        row["check"] for row in first_json(runner.invoke(app, ["-o", "json", "doctor"]).output)
    }
    assert (
        "GitHub API" not in offline and "Permissions" in offline
    )  # network checks only with --online
    runner.invoke(app, ["init"])
    v = first_json(runner.invoke(app, ["-o", "json", "version"]).output)
    assert (
        v["architecture"]
        and v["stratos"]
        and "not published" in v["api_version"]
        and v["api_endpoint"].startswith("https://")
    )
    s = first_json(runner.invoke(app, ["-o", "json", "status"]).output)
    assert s["project"] == folder.name and s["workspaces"] == 0 and s["signed_in"] is True
    (folder / ".stratos" / "project.yaml").write_text("name: [broken\n")
    assert runner.invoke(app, ["doctor"]).exit_code == 9  # an unusable manifest is a failed check


def test_help_lists_layer_d_groups() -> None:
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    for name in ("project", "init", "workspace", "environment", "deploy"):
        assert name in r.output
    for group in ("project", "workspace", "environment", "deploy"):
        assert runner.invoke(app, [group, "--help"]).exit_code == 0
    assert ConfigurationError and DependencyError  # exported for the callers above
