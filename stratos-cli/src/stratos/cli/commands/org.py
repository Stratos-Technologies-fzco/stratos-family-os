"""stratos org get | members | teams | policy."""

import typer

from stratos.cli.context import CliContext

app = typer.Typer(help="View organisation information.", no_args_is_help=True)
OrgOption = typer.Option(None, "--org", help="GitHub organisation (default: configured).")


@app.command()
def get(
    ctx: typer.Context,
    org: str | None = OrgOption,
    refresh: bool = typer.Option(False, "--refresh", help="Bypass the metadata cache."),
) -> None:
    """Show organisation details."""
    cli: CliContext = ctx.obj
    cli.renderer.data(cli.org_service.get(org, refresh=refresh).model_dump(), title="Organisation")


@app.command()
def members(ctx: typer.Context, org: str | None = OrgOption) -> None:
    """List organisation members."""
    cli: CliContext = ctx.obj
    cli.renderer.data([m.model_dump() for m in cli.org_service.members(org)], title="Members")


@app.command()
def teams(ctx: typer.Context, org: str | None = OrgOption) -> None:
    """List teams."""
    cli: CliContext = ctx.obj
    cli.renderer.data([t.model_dump() for t in cli.org_service.teams(org)], title="Teams")


@app.command()
def policy(ctx: typer.Context, org: str | None = OrgOption) -> None:
    """Show organisation policy (GitHub settings and Stratos configuration)."""
    cli: CliContext = ctx.obj
    cli.renderer.data(cli.org_service.policy(org), title="Policy")
