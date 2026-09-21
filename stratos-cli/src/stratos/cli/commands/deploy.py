"""stratos deploy dev | staging | production | status | rollback."""

import typer

from stratos.cli.context import CliContext
from stratos.cli.options import DryRun, Yes
from stratos.domain.models.workflow import DeploymentInfo

app = typer.Typer(help="Deploy, inspect and roll back releases.", no_args_is_help=True)
ProjectOption = typer.Option(None, "--project", help="Project (default: this folder's project).")
OrgOption = typer.Option(None, "--org", help="GitHub organisation (default: configured).")
RefOption = typer.Option(None, "--ref", help="Branch, tag or commit (default: the default branch).")


def _row(d: DeploymentInfo) -> dict[str, object]:
    return {
        "id": d.id,
        "environment": d.environment,
        "ref": d.ref[:12],
        "sha": d.sha[:7],
        "state": d.state,
        "requested_by": d.creator or "-",
        "created": d.created_at or "-",
    }


def _deploy(
    ctx: typer.Context,
    target: str,
    project: str | None,
    ref: str | None,
    org: str | None,
    dry_run: bool,
    yes: bool,
) -> None:
    cli: CliContext = ctx.obj
    name, org = cli.resolve_project(project, org)
    result = cli.deployment_service(dry_run=dry_run, yes=yes).deploy(name, target, ref=ref, org=org)
    if result is None:
        return
    cli.renderer.data(_row(result.deployment), title="Deployment")
    if result.created:
        cli.renderer.success(
            "Deployment requested. Your CI pipeline performs the rollout; "
            "see `stratos deploy status`."
        )
    else:
        cli.renderer.info(
            "An identical deployment is already in progress; nothing new was requested."
        )


@app.command()
def dev(
    ctx: typer.Context,
    project: str | None = ProjectOption,
    ref: str | None = RefOption,
    org: str | None = OrgOption,
    dry_run: bool = DryRun,
    yes: bool = Yes,
) -> None:
    """Deploy to the development environment."""
    _deploy(ctx, "dev", project, ref, org, dry_run, yes)


@app.command()
def staging(
    ctx: typer.Context,
    project: str | None = ProjectOption,
    ref: str | None = RefOption,
    org: str | None = OrgOption,
    dry_run: bool = DryRun,
    yes: bool = Yes,
) -> None:
    """Deploy to staging."""
    _deploy(ctx, "staging", project, ref, org, dry_run, yes)


@app.command()
def production(
    ctx: typer.Context,
    project: str | None = ProjectOption,
    ref: str | None = RefOption,
    org: str | None = OrgOption,
    dry_run: bool = DryRun,
    yes: bool = Yes,
) -> None:
    """Deploy to production (asks for confirmation)."""
    _deploy(ctx, "production", project, ref, org, dry_run, yes)


@app.command()
def status(
    ctx: typer.Context,
    project: str | None = ProjectOption,
    environment: str | None = typer.Option(
        None, "--environment", "-e", help="Only this environment."
    ),
    org: str | None = OrgOption,
) -> None:
    """Latest deployment and its current state per environment."""
    cli: CliContext = ctx.obj
    name, org = cli.resolve_project(project, org)
    found = cli.deployment_service().status(name, environment, org)
    if not found:
        cli.renderer.info("No deployments yet.")
        return
    cli.renderer.data([_row(d) for d in found], title=f"Deployments of {name}")


@app.command()
def rollback(
    ctx: typer.Context,
    environment: str = typer.Option(..., "--environment", "-e", help="Environment to roll back."),
    project: str | None = ProjectOption,
    to: int | None = typer.Option(None, "--to", help="Deployment id to return to."),
    org: str | None = OrgOption,
    dry_run: bool = DryRun,
    yes: bool = Yes,
) -> None:
    """Redeploy the previous successful version (asks for confirmation)."""
    cli: CliContext = ctx.obj
    name, org = cli.resolve_project(project, org)
    created = cli.deployment_service(dry_run=dry_run, yes=yes).rollback(
        name, environment, to=to, org=org
    )
    if created is not None:
        cli.renderer.data(_row(created), title="Rollback deployment")
        cli.renderer.success("Rollback requested. Your CI pipeline performs the rollout.")
