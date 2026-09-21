"""Layer D: project lifecycle (M19), init (M20), registries and shared building blocks."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from devfakes import DevFake, init_adapters
from test_gaps import settings_with
from test_layer_c import (
    GOOD,
    NOW,
    audit_results,
    make_audit,
    make_guard,
    make_require,
    make_skill_registry,
    write_mcp_registry,
)

from stratos.application.init_service import InitService, read_manifest
from stratos.application.mcp_service import McpService
from stratos.application.policy_service import PolicyService
from stratos.application.project_service import STEPS, ProjectService
from stratos.application.skill_service import SkillService
from stratos.domain.enums import ProjectStatus
from stratos.domain.exceptions import (
    APIError,
    AuthorizationError,
    ConfigurationError,
    OperationCancelledError,
    ResourceNotFoundError,
    ValidationError,
)
from stratos.domain.models.workflow import ProjectRecord, ProjectSpec, WorkspaceRecord
from stratos.infrastructure.claude.manager import ClaudeCodeManager
from stratos.infrastructure.filesystem.cache import TtlCache
from stratos.infrastructure.filesystem.config_files import ConfigFileProtector
from stratos.infrastructure.filesystem.registry_store import LocalProjectStore, LocalWorkspaceStore
from stratos.infrastructure.filesystem.skill_registry import LocalSkillRegistry
from stratos.infrastructure.mcp.registry import LocalMcpRegistry
from stratos.utils.managed_block import set_managed_block

ORG = "acme"


def make_project_service(
    tmp_path: Path,
    *roles: str,
    fake: DevFake | None = None,
    policy: PolicyService | None = None,
    skills_root: Path | None = None,
    mcp_root: Path | None = None,
    mcp_allowed: Any = lambda name: True,
    **guard_kw: Any,
) -> tuple[ProjectService, DevFake, LocalProjectStore, Any]:
    fake = fake or DevFake()
    require, ident = make_require(*roles)
    guard, out, err, asked = make_guard(**guard_kw)
    store = LocalProjectStore(tmp_path / "projects.json")
    svc = ProjectService(
        store, fake, require, make_audit(tmp_path, ident), guard,
        policy or PolicyService(settings_with()),
        default_org=ORG, default_environment="development",
        ai_provider="claude", ai_model="claude-sonnet-5",
        knowledge_sources=lambda: ["Markdown folder: docs"],
        skills=lambda: LocalSkillRegistry(skills_root) if skills_root else None,
        mcp=lambda: LocalMcpRegistry(mcp_root) if mcp_root else None,
        mcp_allowed=mcp_allowed, stratos_version="0.1.0", clock=lambda: NOW,
    )  # fmt: skip
    return svc, fake, store, (out, err, asked)


SPEC = ProjectSpec(
    name="api", description="Payments API", template="python", owners=("@acme/platform",)
)


# ================================ M19: create =====================================================
def test_project_create_runs_the_whole_workflow(tmp_path: Path) -> None:
    svc, fake, store, _ = make_project_service(tmp_path, "developer")
    result = svc.create(SPEC)
    assert result and result.created and [s.key for s in result.steps] == [k for k, _ in STEPS]
    project = store.get(ORG, "api")
    assert project and project.status is ProjectStatus.ACTIVE and project.repository == "acme/api"
    assert project.completed_steps == tuple(k for k, _ in STEPS) and project.last_error is None
    assert fake.created == 1 and fake.repos["acme/api"].private is True
    assert (
        fake.protected == ["acme/api@main"] and fake.codeowners["acme/api"] == "* @acme/platform\n"
    )
    assert [lb.name for lb in fake.labels_synced][:2] == ["bug", "feature"]
    assert fake.policy_applied == 1 and set(fake.envs) == {"development"}
    assert "actions/setup-python" in fake.text("api", ".github/workflows/ci.yml")
    claude = fake.text("api", "CLAUDE.md")
    assert "stratos:begin instructions" in claude and "stratos:begin knowledge" in claude
    assert "Markdown folder: docs" in claude
    manifest = yaml.safe_load(fake.text("api", ".stratos/project.yaml"))
    assert manifest["name"] == "api" and manifest["ai"] == {
        "provider": "claude",
        "model": "claude-sonnet-5",
    }
    assert manifest["environments"] == ["development"] and manifest["owners"] == ["@acme/platform"]
    assert audit_results(tmp_path) == [("project.create", "success")]


def test_project_create_is_idempotent(tmp_path: Path) -> None:
    svc, fake, _, _ = make_project_service(tmp_path, "developer")
    svc.create(SPEC)
    again = svc.create(SPEC)
    assert again and not again.created and all(s.status == "already" for s in again.steps)
    assert fake.created == 1 and fake.calls.count("put_file") == fake.calls.count("put_file")
    writes = fake.calls.count("put_file")
    svc.create(SPEC)
    assert fake.calls.count("put_file") == writes  # a finished project causes no further writes
    assert audit_results(tmp_path) == [("project.create", "success")]


def test_project_create_resumes_after_partial_failure(tmp_path: Path) -> None:
    svc, fake, store, _ = make_project_service(tmp_path, "developer")
    fake.fail["protect_branch"] = 1
    with pytest.raises(APIError):
        svc.create(SPEC)
    partial = store.get(ORG, "api")
    assert partial and partial.status is ProjectStatus.PARTIAL
    assert partial.completed_steps == ("register", "repository", "configure_repository")
    assert partial.last_error and partial.last_error.startswith("Configure branch protection")
    assert "abc123secretxyz" not in partial.last_error  # recorded errors are redacted
    assert audit_results(tmp_path) == [("project.create", "failure")]
    result = svc.create(SPEC)  # the same command resumes
    assert result and result.created and result.project.status is ProjectStatus.ACTIVE
    by_key = {s.key: s.status for s in result.steps}
    assert by_key["repository"] == "already" and by_key["branch_protection"] == "done"
    assert fake.created == 1  # the repository was not created twice
    assert store.get(ORG, "api").last_error is None  # type: ignore[union-attr]


def test_project_create_dry_run_writes_nothing(tmp_path: Path) -> None:
    svc, fake, store, (out, err, _) = make_project_service(tmp_path, "developer", dry_run=True)
    assert svc.create(SPEC) is None
    text = out.export_text() + err.export_text()
    assert (
        "DRY RUN" in text
        and "Create GitHub repository [pending]" in text
        and "No changes were made" in text
    )
    assert fake.created == 0 and store.list() == [] and audit_results(tmp_path) == []
    live, _, store2, _ = make_project_service(tmp_path / "b", "developer")
    fake2 = live._gh
    fake2.fail["protect_branch"] = 1  # type: ignore[attr-defined]
    with pytest.raises(APIError):
        live.create(SPEC)
    dry = ProjectService.__new__(ProjectService)
    dry.__dict__.update(live.__dict__)
    dry._guard = make_guard(dry_run=True)[0]
    assert dry.create(SPEC) is None
    assert store2.get(ORG, "api").status is ProjectStatus.PARTIAL  # type: ignore[union-attr]


def test_project_create_validation_and_permissions(tmp_path: Path) -> None:
    svc, fake, _, _ = make_project_service(tmp_path, "developer")
    for bad in ("../x", "a b", ".git", ""):
        with pytest.raises(ValidationError):
            svc.create(ProjectSpec(name=bad))
    with pytest.raises(ValidationError, match="Unknown template"):
        svc.create(ProjectSpec(name="x", template="cobol"))
    with pytest.raises(ValidationError, match="Invalid owner"):
        svc.create(ProjectSpec(name="x", owners=("not an owner",)))
    with pytest.raises(ConfigurationError, match="skills registry"):
        svc.create(ProjectSpec(name="x", skills=("review",)))
    with pytest.raises(ConfigurationError, match="MCP registry"):
        svc.create(ProjectSpec(name="x", mcp_servers=("github",)))
    assert fake.created == 0
    with pytest.raises(AuthorizationError):
        make_project_service(tmp_path / "v", "viewer")[0].create(SPEC)


def test_public_projects_need_policy_and_confirmation(tmp_path: Path) -> None:
    blocked, fake, store, _ = make_project_service(
        tmp_path,
        "developer",
        policy=PolicyService(settings_with(allow_public_repos=False)),
        yes=True,
    )
    with pytest.raises(AuthorizationError, match="organisation policy"):
        blocked.create(ProjectSpec(name="site", private=False))
    assert (
        fake.created == 0
        and store.list() == []
        and audit_results(tmp_path) == [("project.create", "denied")]
    )
    asking, fake2, store2, (_, _, asked) = make_project_service(
        tmp_path / "c", "developer", answer=False
    )
    with pytest.raises(OperationCancelledError):
        asking.create(ProjectSpec(name="site", private=False))
    assert asked == ["Continue?"] and fake2.created == 0
    ok, fake3, _, _ = make_project_service(tmp_path / "d", "developer", yes=True)
    result = ok.create(ProjectSpec(name="site", private=False))
    assert result and not result.project.private and fake3.repos["acme/site"].private is False


def test_project_create_keeps_existing_ci_and_claude_files(tmp_path: Path) -> None:
    svc, fake, _, _ = make_project_service(tmp_path, "developer")
    fake.repos["acme/api"] = __import__(
        "stratos.domain.models.github", fromlist=["Repository"]
    ).Repository(name="api", full_name="acme/api")
    fake.files[("api", ".github/workflows/ci.yml")] = b"name: My own CI\n"
    fake.files[("api", "CLAUDE.md")] = b"# Team notes\nKeep me.\n"
    result = svc.create(SPEC)
    assert result and {s.key: s.status for s in result.steps}["cicd"] == "skipped"
    assert fake.text("api", ".github/workflows/ci.yml") == "name: My own CI\n"
    claude = fake.text("api", "CLAUDE.md")
    assert "Keep me." in claude and "Organisation instructions" in claude and fake.created == 0


def test_project_create_with_skills_and_mcp_commits_verified_files(tmp_path: Path) -> None:
    skills, mcp = tmp_path / "skills", tmp_path / "mcp"
    skills.mkdir()
    files = make_skill_registry(skills)
    write_mcp_registry(mcp, [GOOD])
    svc, fake, store, _ = make_project_service(
        tmp_path, "maintainer", skills_root=skills, mcp_root=mcp
    )
    fake.files[("api", ".mcp.json")] = json.dumps(
        {"mcpServers": {"mine": {"command": "x"}}, "other": 1}
    ).encode()
    result = svc.create(ProjectSpec(name="api", skills=("review",), mcp_servers=("github",)))
    assert result and result.project.skills == ("review",)
    assert fake.files[("api", ".claude/skills/review/SKILL.md")] == files["SKILL.md"]
    assert fake.files[("api", ".claude/skills/review/notes/a.txt")] == files["notes/a.txt"]
    mcp_json = json.loads(fake.text("api", ".mcp.json"))
    assert set(mcp_json["mcpServers"]) == {"mine", "github"} and mcp_json["other"] == 1
    assert mcp_json["mcpServers"]["github"]["env"] == {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}


def test_project_create_needs_the_right_roles_for_skills_and_mcp(tmp_path: Path) -> None:
    skills, mcp = tmp_path / "skills", tmp_path / "mcp"
    skills.mkdir()
    make_skill_registry(skills)
    write_mcp_registry(mcp, [GOOD])
    dev, fake, _, _ = make_project_service(tmp_path, "developer", skills_root=skills, mcp_root=mcp)
    with pytest.raises(AuthorizationError):  # MCP installation is a maintainer action
        dev.create(ProjectSpec(name="api", mcp_servers=("github",)))
    assert fake.created == 0
    assert (
        dev.create(ProjectSpec(name="api", skills=("review",))) is not None
    )  # skills are developer-level


def test_project_create_rejects_tampered_skills_secrets_and_policy(tmp_path: Path) -> None:
    skills, mcp = tmp_path / "skills", tmp_path / "mcp"
    skills.mkdir()
    make_skill_registry(skills, tamper=True)
    svc, fake, store, _ = make_project_service(tmp_path, "maintainer", skills_root=skills)
    with pytest.raises(ValidationError, match="Checksum mismatch"):
        svc.create(ProjectSpec(name="api", skills=("review",)))
    assert not any(k[1].startswith(".claude/skills") for k in fake.files)
    assert store.get(ORG, "api").status is ProjectStatus.PARTIAL  # type: ignore[union-attr]
    write_mcp_registry(mcp, [{**GOOD, "env": {"API_KEY": "sk-" + "a" * 30}}])
    leaky, fake2, _, _ = make_project_service(tmp_path / "b", "maintainer", mcp_root=mcp)
    with pytest.raises(ValidationError):
        leaky.create(ProjectSpec(name="api", mcp_servers=("github",)))
    assert ("api", ".mcp.json") not in fake2.files  # secrets never reach the repository
    write_mcp_registry(tmp_path / "mcp2", [GOOD])
    blocked, fake3, _, _ = make_project_service(
        tmp_path / "c", "maintainer", mcp_root=tmp_path / "mcp2", mcp_allowed=lambda n: False
    )
    with pytest.raises(AuthorizationError, match="organisation policy"):
        blocked.create(ProjectSpec(name="api", mcp_servers=("github",)))
    (tmp_path / "d").mkdir()
    bad_json, fake4, _, _ = make_project_service(
        tmp_path / "d", "maintainer", mcp_root=tmp_path / "mcp2"
    )
    fake4.files[("api", ".mcp.json")] = b"{not json"
    with pytest.raises(ConfigurationError, match="not valid JSON"):
        bad_json.create(ProjectSpec(name="api", mcp_servers=("github",)))
    assert fake4.files[("api", ".mcp.json")] == b"{not json"  # left untouched


def test_project_ai_step_respects_policy(tmp_path: Path) -> None:
    policy = PolicyService(settings_with(allowed_ai_models=["approved-only"]))
    svc, fake, store, _ = make_project_service(tmp_path, "developer", policy=policy)
    with pytest.raises(AuthorizationError, match="claude-sonnet-5"):
        svc.create(SPEC)
    assert store.get(ORG, "api").status is ProjectStatus.PARTIAL  # type: ignore[union-attr]
    assert "CLAUDE.md" not in {k[1] for k in fake.files}  # later steps did not run


# ================================ M19: list, get, update, delete ========================================
def test_project_list_get_update(tmp_path: Path) -> None:
    svc, fake, store, _ = make_project_service(tmp_path, "developer")
    svc.create(SPEC)
    svc.create(ProjectSpec(name="web"))
    assert [p.name for p in svc.list_projects()] == ["api", "web"]
    assert svc.get("api").repository == "acme/api"
    with pytest.raises(ResourceNotFoundError):
        svc.get("ghost")
    updated = svc.update("api", description="New text", owners=("@acme/sec",))
    assert updated and updated.description == "New text" and updated.owners == ("@acme/sec",)
    assert (
        fake.descriptions["acme/api"] == "New text"
        and fake.codeowners["acme/api"] == "* @acme/sec\n"
    )
    assert ("project.update", "success") in audit_results(tmp_path)
    with pytest.raises(ValidationError, match="Nothing to update"):
        svc.update("api")
    with pytest.raises(ValidationError, match="Invalid owner"):
        svc.update("api", owners=("bad owner",))
    with pytest.raises(ResourceNotFoundError):
        svc.update("ghost", description="x")
    with pytest.raises(AuthorizationError):
        make_project_service(tmp_path / "v", "viewer")[0].update("api", description="x")
    assert store.get(ORG, "api").description == "New text"  # type: ignore[union-attr]


def test_project_update_dry_run(tmp_path: Path) -> None:
    svc, fake, store, _ = make_project_service(tmp_path, "developer")
    svc.create(SPEC)
    svc._guard = make_guard(dry_run=True)[0]
    assert svc.update("api", description="Changed") is None
    assert (
        "acme/api" not in fake.descriptions and store.get(ORG, "api").description == "Payments API"
    )  # type: ignore[union-attr]


def test_project_delete_confirms_audits_and_keeps_the_repository(tmp_path: Path) -> None:
    svc, fake, store, _ = make_project_service(tmp_path, "maintainer", answer=False)
    svc.create(SPEC)
    with pytest.raises(OperationCancelledError):
        svc.delete("api")
    assert store.get(ORG, "api") is not None  # cancelled: nothing removed
    yes, fake2, store2, _ = make_project_service(tmp_path / "b", "maintainer", yes=True)
    yes.create(SPEC)
    removed = yes.delete("api")
    assert removed and store2.get(ORG, "api") is None and not fake2.repos["acme/api"].archived
    assert ("project.delete", "success") in audit_results(tmp_path / "b")
    with pytest.raises(ResourceNotFoundError):
        yes.delete("api")


def test_project_delete_can_archive_and_needs_roles(tmp_path: Path) -> None:
    svc, fake, store, _ = make_project_service(tmp_path, "maintainer", yes=True)
    svc.create(SPEC)
    svc.delete("api", archive_repo=True)
    assert fake.repos["acme/api"].archived and store.get(ORG, "api") is None
    dev, _, _, _ = make_project_service(tmp_path / "d", "developer", yes=True)
    dev.create(SPEC)
    with pytest.raises(AuthorizationError):
        dev.delete("api")
    dry, fake3, store3, _ = make_project_service(tmp_path / "e", "maintainer", dry_run=True)
    store3.save(ProjectRecord(name="api", org=ORG, repository="acme/api"))
    assert dry.delete("api") is None and store3.get(ORG, "api") is not None


# ================================ M20: init ===================================================================
def make_init(
    tmp_path: Path,
    *roles: str,
    project: Path | None = None,
    skills: Any = None,
    mcp: Any = None,
    **guard_kw: Any,
) -> tuple[InitService, Path, Any]:
    folder = project or tmp_path / "My Folder"
    folder.mkdir(parents=True, exist_ok=True)
    require, ident = make_require(*(roles or ("developer",)))
    guard, out, err, _ = make_guard(**guard_kw)
    svc = InitService(
        folder, require, make_audit(tmp_path, ident), guard, org=ORG, ai_provider="claude", ai_model="m",
        instructions="Org rule one", knowledge_sources=lambda: ["Markdown folder: docs"],
        skills=skills, mcp=mcp, **init_adapters(),
    )  # fmt: skip
    return svc, folder, (out, err)


def statuses(items: list[Any]) -> dict[str, str]:
    return {i.name: i.status for i in items}


def test_init_sets_up_an_empty_folder_and_is_repeatable(tmp_path: Path) -> None:
    svc, folder, _ = make_init(tmp_path)
    (folder / "pyproject.toml").write_text("[project]\nname='x'\n")
    first = statuses(svc.init())
    assert set(first.values()) == {"created"} or "created" in first.values()
    manifest = read_manifest(folder)
    assert manifest and manifest.name == "My-Folder" and manifest.template == "python"
    assert manifest.organisation == ORG and manifest.ai == {"provider": "claude", "model": "m"}
    claude = (folder / "CLAUDE.md").read_text()
    assert "Org rule one" in claude and "stratos knowledge search" in claude
    ignore = (folder / ".gitignore").read_text()
    assert ".stratos/backups/" in ignore and ".env" in ignore
    settings = json.loads((folder / ".claude/settings.json").read_text())
    assert "Read(./.env)" in settings["permissions"]["deny"]
    snapshot = {
        p: p.read_bytes()
        for p in folder.rglob("*")
        if p.is_file() and ".stratos/backups" not in p.as_posix()
    }
    second = statuses(svc.init())
    assert set(second.values()) <= {"unchanged", "present"}  # nothing to do the second time
    assert {p: p.read_bytes() for p in snapshot} == snapshot  # and nothing was rewritten
    assert audit_results(tmp_path)[0] == ("project.init", "success")


def test_init_preserves_developer_files_and_shows_a_diff(tmp_path: Path) -> None:
    svc, folder, _ = make_init(tmp_path)
    (folder / "CLAUDE.md").write_text("# Our team\nKeep this text.\n")
    (folder / ".gitignore").write_text("node_modules/\n*.log")  # no trailing newline
    (folder / ".claude").mkdir()
    (folder / ".claude/settings.json").write_text(
        json.dumps(
            {
                "permissions": {"deny": ["Read(./secrets.txt)"], "allow": ["Bash(ls)"]},
                "model": "opus",
            }
        )
    )
    items = {i.name: i for i in svc.init()}
    assert "Keep this text." in (folder / "CLAUDE.md").read_text()
    assert (folder / ".gitignore").read_text().startswith("node_modules/\n*.log\n")
    settings = json.loads((folder / ".claude/settings.json").read_text())
    assert (
        settings["permissions"]["deny"][0] == "Read(./secrets.txt)"
        and "Read(./.env)" in settings["permissions"]["deny"]
    )
    assert settings["permissions"]["allow"] == ["Bash(ls)"] and settings["model"] == "opus"
    claude_diff = items["CLAUDE.md instructions"].diff
    assert items["CLAUDE.md instructions"].status == "updated" and "+Org rule one" in claude_diff
    assert list((folder / ".stratos" / "backups").glob("CLAUDE.md.*.bak"))  # a backup was taken


def test_init_dry_run_shows_the_plan_and_writes_nothing(tmp_path: Path) -> None:
    svc, folder, (out, err) = make_init(tmp_path, dry_run=True)
    (folder / "CLAUDE.md").write_text("# Mine\n")
    before = {p.name for p in folder.rglob("*")}
    items = svc.init(skills=("review",), mcp=("github",))
    assert {p.name for p in folder.rglob("*")} == before and (
        folder / "CLAUDE.md"
    ).read_text() == "# Mine\n"
    assert (
        "would-change" in statuses(items).values() and statuses(items)["Skills"] == "would-change"
    )
    text = out.export_text() + err.export_text()
    assert (
        "DRY RUN" in text
        and "CLAUDE.md instructions: would-change" in text
        and audit_results(tmp_path) == []
    )


def test_init_refuses_an_invalid_manifest_without_touching_anything(tmp_path: Path) -> None:
    svc, folder, _ = make_init(tmp_path)
    (folder / ".stratos").mkdir()
    (folder / ".stratos/project.yaml").write_text("name: [broken\n")
    with pytest.raises(ConfigurationError, match="not a valid Stratos manifest"):
        svc.init()
    assert (folder / ".stratos/project.yaml").read_text() == "name: [broken\n"
    assert not (folder / "CLAUDE.md").exists()  # nothing else was written
    assert svc.detect()["Stratos project"] == "invalid manifest"


def test_init_rolls_everything_back_when_a_later_step_fails(tmp_path: Path) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / "CLAUDE.md").write_text("# Original\n")
    reg = tmp_path / "registry"
    reg.mkdir()
    make_skill_registry(reg)
    require, ident = make_require("developer")
    guard, *_ = make_guard(yes=True)
    protector = ConfigFileProtector()
    skill_service = SkillService(
        LocalSkillRegistry(reg), ClaudeCodeManager(folder, protector), protector, require,
        make_audit(tmp_path, ident), guard, TtlCache(tmp_path / "c"), stratos_version="0.1.0", registry_id="r",
    )  # fmt: skip
    svc, _, _ = make_init(tmp_path, project=folder, skills=lambda: skill_service, yes=True)
    with pytest.raises(ResourceNotFoundError):
        svc.init(skills=("does-not-exist",))
    assert (folder / "CLAUDE.md").read_text() == "# Original\n"  # restored from the backup
    assert not (folder / ".stratos/project.yaml").exists() and not (folder / ".gitignore").exists()
    assert not (folder / ".claude/settings.json").exists()
    assert ("project.init", "failure") in audit_results(tmp_path)


def test_init_installs_skills_and_mcp_servers_through_their_services(tmp_path: Path) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    reg, mcp_reg = tmp_path / "skills", tmp_path / "mcp"
    reg.mkdir()
    make_skill_registry(reg)
    write_mcp_registry(mcp_reg, [GOOD])
    require, ident = make_require("maintainer")
    guard, *_ = make_guard(yes=True)
    protector = ConfigFileProtector()
    claude = ClaudeCodeManager(folder, protector)
    audit = make_audit(tmp_path, ident)
    skill_service = SkillService(LocalSkillRegistry(reg), claude, protector, require, audit, guard,
                                 TtlCache(tmp_path / "c1"), stratos_version="0.1.0", registry_id="s")  # fmt: skip
    mcp_service = McpService(LocalMcpRegistry(mcp_reg), claude, require, audit, guard, TtlCache(tmp_path / "c2"),
                             allowed=None, environ={}, registry_id="m")  # fmt: skip
    svc, _, _ = make_init(
        tmp_path,
        "maintainer",
        project=folder,
        skills=lambda: skill_service,
        mcp=lambda: mcp_service,
        yes=True,
    )
    first = statuses(svc.init(skills=("review",), mcp=("github",)))
    assert first["Skill review"] == "installed" and first["MCP server github"] in {
        "created",
        "updated",
    }
    assert (folder / ".claude/skills/review/SKILL.md").exists()
    assert "github" in json.loads((folder / ".mcp.json").read_text())["mcpServers"]
    second = statuses(svc.init(skills=("review",), mcp=("github",)))
    assert second["Skill review"] == "unchanged" and second["MCP server github"] == "unchanged"


def test_init_detects_what_is_already_in_place(tmp_path: Path) -> None:
    svc, folder, _ = make_init(tmp_path)
    (folder / ".git").mkdir()
    (folder / ".editorconfig").write_text("root = true\n")
    (folder / ".env.example").write_text("A=1\n")
    (folder / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    facts = svc.detect()
    assert facts["Git repository"] == "yes" and facts["Stratos project"] == "no"
    assert ".editorconfig" in facts["Coding standards"] and "tool.ruff" in facts["Coding standards"]
    assert facts["Environment file"] == ".env.example" and facts["Instructions (CLAUDE.md)"] == "no"
    svc.init()
    assert (
        svc.detect()["Stratos project"].startswith("yes")
        and svc.detect()["Instructions (CLAUDE.md)"] == "yes"
    )


def test_init_validates_template_and_project_name(tmp_path: Path) -> None:
    svc, folder, _ = make_init(tmp_path)
    with pytest.raises(ValidationError, match="Unknown template"):
        svc.init(template="cobol")
    (folder.parent / "!!!").mkdir()
    weird, _, _ = make_init(tmp_path, project=folder.parent / "!!!")
    weird.init()
    assert (
        read_manifest(folder.parent / "!!!").name == "project"
    )  # unusable folder names fall back  # type: ignore[union-attr]
    named, f2, _ = make_init(tmp_path, project=tmp_path / "other")
    named.init(name="Custom.Name", template="node")
    manifest = read_manifest(f2)
    assert manifest and manifest.name == "Custom.Name" and manifest.template == "node"


# ================================ registries and building blocks =================================================
def test_local_project_store_crud_ordering_and_atomicity(tmp_path: Path) -> None:
    store = LocalProjectStore(tmp_path / "d" / "projects.json")
    assert (
        store.list() == [] and store.get("acme", "x") is None and store.delete("acme", "x") is False
    )
    for name in ("web", "Api", "cli"):
        store.save(ProjectRecord(name=name, org="acme", repository=f"acme/{name}"))
    store.save(ProjectRecord(name="other", org="beta"))
    assert [p.name for p in store.list("acme")] == ["Api", "cli", "web"] and len(store.list()) == 4
    store.save(ProjectRecord(name="web", org="acme", description="changed"))
    assert store.get("acme", "web").description == "changed" and len(store.list("acme")) == 3  # type: ignore[union-attr]
    assert store.delete("acme", "web") and store.get("acme", "web") is None
    assert not list((tmp_path / "d").glob(".stratos-*"))  # no temp files left behind


def test_registries_refuse_to_overwrite_a_damaged_file(tmp_path: Path) -> None:
    path = tmp_path / "projects.json"
    path.write_text("{ this is not json")
    store = LocalProjectStore(path)
    with pytest.raises(ConfigurationError, match="damaged"):
        store.list()
    with pytest.raises(ConfigurationError, match="damaged"):
        store.save(ProjectRecord(name="x", org="acme"))
    assert path.read_text() == "{ this is not json"  # never silently replaced
    path.write_text(json.dumps({"projects": {"acme/x": {"name": 5}}}))
    with pytest.raises(ConfigurationError):
        LocalProjectStore(path).list()
    wpath = tmp_path / "ws.json"
    wpath.write_text("[1,2]")
    with pytest.raises(ConfigurationError, match="damaged"):
        LocalWorkspaceStore(wpath).list()


def test_local_workspace_store_crud(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / "ws.json")
    assert store.list() == [] and store.get("a") is None and store.delete("a") is False
    for name in ("zeta", "Alpha"):
        store.save(WorkspaceRecord(name=name, project="acme/api", path=str(tmp_path / name)))
    assert [w.name for w in store.list()] == ["Alpha", "zeta"]
    assert store.delete("zeta") and store.get("zeta") is None and store.get("Alpha") is not None


def test_managed_block_is_pure_and_protector_dry_run_writes_nothing(tmp_path: Path) -> None:
    once = set_managed_block("# Mine\n", "x", "one")
    twice = set_managed_block(once, "x", "two")
    assert (
        "# Mine" in twice
        and "two" in twice
        and "one" not in twice
        and twice.count("stratos:begin x") == 1
    )
    assert set_managed_block(twice, "x", "two") == twice  # idempotent
    other = set_managed_block(twice, "y", "side")
    assert "two" in other and "side" in other
    dry = ConfigFileProtector(dry_run=True)
    target = tmp_path / "f.md"
    target.write_text("old\n")
    result = dry.write_text(target, "new\n")
    assert result.changed and "+new" in result.diff and target.read_text() == "old\n"
    assert (
        not dry.write_text(tmp_path / "missing.md", "x").backup
        and not (tmp_path / "missing.md").exists()
    )
    assert dry.write_bytes(tmp_path / "b.bin", b"1").changed and not (tmp_path / "b.bin").exists()
    assert (
        dry.merge_json(tmp_path / "s.json", {"a": 1}).changed and not (tmp_path / "s.json").exists()
    )
    assert (
        dry.write_managed_block(tmp_path / "m.md", "id", "x").changed
        and not (tmp_path / "m.md").exists()
    )
