"""Projects, init, workspaces, environments and deployments (M19-M22)."""

from pathlib import Path
from typing import TYPE_CHECKING

from stratos.domain.exceptions import ValidationError
from stratos.domain.interfaces import ClaudeProject, FileProtector

if TYPE_CHECKING:
    from stratos.application.deployment_service import DeploymentService, EnvironmentService
    from stratos.application.init_service import InitService
    from stratos.application.project_service import ProjectService
    from stratos.application.workspace_service import WorkspaceService
    from stratos.cli.context import CliContext


def org_instructions(ctx: "CliContext") -> str:
    from stratos.domain.standards import DEFAULT_INSTRUCTIONS
    from stratos.utils.files import read_text_limited

    configured = ctx.settings.project.instructions_file
    if not configured:
        return DEFAULT_INSTRUCTIONS
    return read_text_limited(Path(configured).expanduser(), 50_000)


def project_service(ctx: "CliContext", *, dry_run: bool, yes: bool) -> "ProjectService":
    from stratos import __version__
    from stratos.application.project_service import ProjectService
    from stratos.infrastructure.filesystem.skill_registry import LocalSkillRegistry
    from stratos.infrastructure.mcp.registry import LocalMcpRegistry

    s = ctx.settings
    allowed = s.mcp.allowed
    skills_root, mcp_root = s.skills.registry, s.mcp.registry
    return ProjectService(
        ctx.project_store,
        ctx.github,
        ctx.require,
        ctx.audit,
        ctx.guard(dry_run=dry_run, yes=yes),
        ctx.policy,
        default_org=ctx.default_org,
        default_environment=s.defaults.environment,
        ai_provider=s.ai.provider,
        ai_model=s.ai.model,
        instructions=org_instructions(ctx),
        knowledge_sources=ctx.knowledge_source_labels,
        skills=lambda: LocalSkillRegistry(Path(skills_root).expanduser()) if skills_root else None,
        mcp=lambda: LocalMcpRegistry(Path(mcp_root).expanduser()) if mcp_root else None,
        mcp_allowed=lambda name: allowed is None or name in allowed,
        stratos_version=__version__,
    )


def init_service(
    ctx: "CliContext", project_dir: Path | None, *, dry_run: bool, yes: bool
) -> "InitService":
    from stratos.application.init_service import InitService
    from stratos.infrastructure.claude.manager import ClaudeCodeManager
    from stratos.infrastructure.filesystem.config_files import ConfigFileProtector

    s = ctx.settings
    target = project_dir or ctx.project_dir

    def make_protector(dry: bool) -> FileProtector:
        return ConfigFileProtector(ctx.redactor, dry_run=dry)

    def make_claude(folder: Path, protector: FileProtector) -> ClaudeProject:
        assert isinstance(protector, ConfigFileProtector)
        return ClaudeCodeManager(folder, protector)

    return InitService(
        target,
        ctx.require,
        ctx.audit,
        ctx.guard(dry_run=dry_run, yes=yes),
        org=ctx.default_org,
        ai_provider=s.ai.provider,
        ai_model=s.ai.model,
        instructions=org_instructions(ctx),
        knowledge_sources=ctx.knowledge_source_labels,
        skills=lambda: ctx.skill_service(yes=yes, project_dir=target),
        mcp=lambda: ctx.mcp_service(yes=yes, project_dir=target),
        protector_factory=make_protector,
        claude_factory=make_claude,
        redactor=ctx.redactor,
    )


def workspace_service(ctx: "CliContext", *, dry_run: bool, yes: bool) -> "WorkspaceService":
    from stratos.application.workspace_service import WorkspaceService
    from stratos.infrastructure.github.git import clone_repository

    configured = ctx.settings.workspace.root
    root = Path(configured).expanduser() if configured else Path.home() / "stratos-workspaces"
    return WorkspaceService(
        ctx.workspace_store,
        ctx.project_store,
        ctx.require,
        ctx.audit,
        ctx.guard(dry_run=dry_run, yes=yes),
        root=root,
        default_org=ctx.default_org,
        clone=lambda org, name, dest: clone_repository(org, name, dest),
        init_factory=lambda path: ctx.init_service(path, dry_run=dry_run, yes=yes),
    )


def environment_service(ctx: "CliContext", *, dry_run: bool, yes: bool) -> "EnvironmentService":
    from stratos.application.deployment_service import EnvironmentService

    return EnvironmentService(
        ctx.project_store,
        ctx.github,
        ctx.require,
        ctx.audit,
        ctx.guard(dry_run=dry_run, yes=yes),
        default_org=ctx.default_org,
        default_environment=ctx.settings.defaults.environment,
    )


def deployment_service(ctx: "CliContext", *, dry_run: bool, yes: bool) -> "DeploymentService":
    from stratos.application.deployment_service import DeploymentService

    return DeploymentService(
        ctx.project_store,
        ctx.github,
        ctx.require,
        ctx.audit,
        ctx.guard(dry_run=dry_run, yes=yes),
        default_org=ctx.default_org,
        default_environment=ctx.settings.defaults.environment,
    )


def resolve_project(
    ctx: "CliContext", project: str | None, org: str | None
) -> tuple[str, str | None]:
    """Project name (and organisation): the option, else this folder's manifest."""
    from stratos.application.init_service import read_manifest

    if project:
        return project, org
    manifest = read_manifest(ctx.project_dir)
    if manifest is None:
        raise ValidationError(
            "No project specified.",
            hint="Use --project NAME, or run inside a folder set up with `stratos init`.",
        )
    return manifest.name, org or manifest.organisation or None
