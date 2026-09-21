"""stratos environment list | create | get | delete."""

import typer

from stratos.cli.context import CliContext
from stratos.cli.options import DryRun, Yes
from stratos.domain.models.workflow import EnvironmentInfo

app = typer.Typer(help="Manage a project's environments.", no_args_is_help=True)
ProjectOption = typer.Option(None, "--project", help="Project (default: this folder's project).")
OrgOption = typer.Option(None, "--org", help="GitHub organisation (default: configured).")


def _row(e: EnvironmentInfo) -> dict[str, object]:
    return {"name": e.name, "protected": e.protected, "url": e.url or "-"}


@app.command("list")
def list_(
    ctx: typer.Context, project: str | None = ProjectOption, org: str | None = OrgOption
) -> None:
    """List environments."""
    cli: CliContext = ctx.obj
    name, org = cli.resolve_project(project, org)
    envs = cli.environment_service().list_environments(name, org)
    cli.renderer.data([_row(e) for e in envs], title=f"Environments of {name}")


@app.command()
def create(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Environment (default: the configured default)."),
    project: str | None = ProjectOption,
    org: str | None = OrgOption,
    dry_run: bool = DryRun,
) -> None:
    """Create an environment (safe to re-run). `dev` means `development`."""
    cli: CliContext = ctx.obj
    project_name, org = cli.resolve_project(project, org)
    result = cli.environment_service(dry_run=dry_run).create(project_name, name, org)
    if result is None:
        return
    env, created = result
    cli.renderer.success(
        f"Environment '{env.name}' " + ("created." if created else "already exists.")
    )


@app.command()
def get(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Environment (default: the configured default)."),
    project: str | None = ProjectOption,
    org: str | None = OrgOption,
) -> None:
    """Show one environment."""
    cli: CliContext = ctx.obj
    project_name, org = cli.resolve_project(project, org)
    cli.renderer.data(
        _row(cli.environment_service().get(project_name, name, org)), title="Environment"
    )


@app.command()
def delete(
    ctx: typer.Context,
    name: str,
    project: str | None = ProjectOption,
    org: str | None = OrgOption,
    dry_run: bool = DryRun,
    yes: bool = Yes,
) -> None:
    """Delete an environment (asks for confirmation)."""
    cli: CliContext = ctx.obj
    project_name, org = cli.resolve_project(project, org)
    removed = cli.environment_service(dry_run=dry_run, yes=yes).delete(project_name, name, org)
    if removed:
        cli.renderer.success(f"Environment '{removed}' deleted.")
