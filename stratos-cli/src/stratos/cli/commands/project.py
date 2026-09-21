"""stratos project create | list | get | update | delete | init, and the top-level stratos init."""

import typer

from stratos.application.init_service import InitItem
from stratos.cli.context import CliContext
from stratos.cli.options import DryRun, Yes
from stratos.domain.models.workflow import ProjectRecord, ProjectSpec

app = typer.Typer(help="Create and manage projects.", no_args_is_help=True)
init_app = typer.Typer()
OrgOption = typer.Option(None, "--org", help="GitHub organisation (default: configured).")


def _row(p: ProjectRecord) -> dict[str, object]:
    return {
        "name": p.name,
        "repository": p.repository or "-",
        "status": p.status.value,
        "visibility": "private" if p.private else "public",
        "environments": ", ".join(p.environments) or "-",
    }


@app.command()
def create(
    ctx: typer.Context,
    name: str,
    description: str = typer.Option("", "--description"),
    public: bool = typer.Option(False, "--public", help="Create a public repository."),
    template: str | None = typer.Option(
        None, "--template", help="CI template: python, node, none."
    ),
    owners: list[str] = typer.Option([], "--owner", help="CODEOWNERS entry (@user or @org/team)."),
    skill: list[str] = typer.Option([], "--skill", help="Skill to add (needs skills.registry)."),
    mcp: list[str] = typer.Option([], "--mcp", help="MCP server to add (needs mcp.registry)."),
    org: str | None = OrgOption,
    dry_run: bool = DryRun,
    yes: bool = Yes,
) -> None:
    """Create a project end to end: repository, protection, CI, environment, Claude setup.

    Safe to re-run: a project that stopped part-way resumes where it left off.
    """
    cli: CliContext = ctx.obj
    settings = cli.settings
    spec = ProjectSpec(
        name=name,
        description=description,
        private=not public,
        template=template or settings.project.template,
        owners=tuple(owners or settings.project.default_owners),
        skills=tuple(skill),
        mcp_servers=tuple(mcp),
    )
    result = cli.project_service(dry_run=dry_run, yes=yes).create(spec, org=org)
    if result is None:
        return
    cli.renderer.data(
        [{"step": s.title, "result": s.status, "detail": s.detail} for s in result.steps],
        title=f"Project {result.project.key}",
    )
    if result.created:
        cli.renderer.success(
            f"Project {result.project.key} is ready ({result.project.repository})."
        )
    else:
        cli.renderer.info(f"Project {result.project.key} already exists and is fully set up.")


@app.command("list")
def list_(ctx: typer.Context, org: str | None = OrgOption) -> None:
    """List registered projects."""
    cli: CliContext = ctx.obj
    cli.renderer.data([_row(p) for p in cli.project_service().list_projects(org)], title="Projects")


@app.command()
def get(ctx: typer.Context, name: str, org: str | None = OrgOption) -> None:
    """Show one project."""
    cli: CliContext = ctx.obj
    record = cli.project_service().get(name, org)
    data = record.model_dump(mode="json")
    for key in ("owners", "skills", "mcp_servers", "environments", "completed_steps"):
        data[key] = ", ".join(data[key]) or "-"
    cli.renderer.data(data, title=record.key)


@app.command()
def update(
    ctx: typer.Context,
    name: str,
    description: str | None = typer.Option(None, "--description"),
    owners: list[str] = typer.Option([], "--owner", help="Replace CODEOWNERS entries."),
    org: str | None = OrgOption,
    dry_run: bool = DryRun,
) -> None:
    """Update a project's description and/or CODEOWNERS."""
    cli: CliContext = ctx.obj
    record = cli.project_service(dry_run=dry_run).update(
        name, description=description, owners=tuple(owners) if owners else None, org=org
    )
    if record is not None:
        cli.renderer.success(f"Project {record.key} updated.")


@app.command()
def delete(
    ctx: typer.Context,
    name: str,
    archive_repo: bool = typer.Option(
        False, "--archive-repo", help="Also archive the GitHub repository."
    ),
    org: str | None = OrgOption,
    dry_run: bool = DryRun,
    yes: bool = Yes,
) -> None:
    """Remove a project from the registry (the repository is kept unless --archive-repo)."""
    cli: CliContext = ctx.obj
    record = cli.project_service(dry_run=dry_run, yes=yes).delete(
        name, archive_repo=archive_repo, org=org
    )
    if record is not None:
        cli.renderer.success(f"Project {record.key} removed.")


# ---- init -------------------------------------------------------------------------------
def _show(cli: CliContext, items: list[InitItem], *, diff: bool) -> None:
    cli.renderer.data(
        [{"item": i.name, "status": i.status, "detail": i.detail} for i in items],
        title="Stratos setup",
    )
    for item in items:
        if diff and item.diff:
            cli.renderer.info(f"--- {item.name} ---")
            for line in item.diff.rstrip().splitlines():
                cli.renderer.info(line)


def run_init(
    ctx: typer.Context,
    name: str | None,
    template: str | None,
    skill: list[str],
    mcp: list[str],
    dry_run: bool,
    diff: bool,
    yes: bool,
) -> None:
    cli: CliContext = ctx.obj
    service = cli.init_service(dry_run=dry_run, yes=yes)
    facts = service.detect()
    cli.renderer.data(facts, title="Detected")
    items = service.init(name=name, template=template, skills=tuple(skill), mcp=tuple(mcp))
    _show(cli, items, diff=diff or dry_run)
    changed = [i for i in items if i.status in {"created", "updated"}]
    if not dry_run:
        cli.renderer.success(
            f"{len(changed)} item(s) changed." if changed else "Already set up; nothing to change."
        )


InitName = typer.Option(None, "--name", help="Project name (default: the folder name).")
InitTemplate = typer.Option(None, "--template", help="python, node or none (default: detected).")
InitSkill = typer.Option([], "--skill", help="Skill to install.")
InitMcp = typer.Option([], "--mcp", help="MCP server to install.")
InitDiff = typer.Option(False, "--diff", help="Show what changed in existing files.")


@app.command("init")
def project_init(
    ctx: typer.Context,
    name: str | None = InitName,
    template: str | None = InitTemplate,
    skill: list[str] = InitSkill,
    mcp: list[str] = InitMcp,
    dry_run: bool = DryRun,
    diff: bool = InitDiff,
    yes: bool = Yes,
) -> None:
    """Bring this folder into the Stratos setup (same as `stratos init`)."""
    run_init(ctx, name, template, skill, mcp, dry_run, diff, yes)


@init_app.command("init")
def init_command(
    ctx: typer.Context,
    name: str | None = InitName,
    template: str | None = InitTemplate,
    skill: list[str] = InitSkill,
    mcp: list[str] = InitMcp,
    dry_run: bool = DryRun,
    diff: bool = InitDiff,
    yes: bool = Yes,
) -> None:
    """Bring this folder into the Stratos setup. Safe to run repeatedly."""
    run_init(ctx, name, template, skill, mcp, dry_run, diff, yes)
