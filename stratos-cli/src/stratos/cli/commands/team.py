"""stratos team list | get | members | permissions."""

import typer

from stratos.cli.context import CliContext

app = typer.Typer(help="View teams and their access.", no_args_is_help=True)
OrgOption = typer.Option(None, "--org", help="GitHub organisation (default: configured).")


@app.command("list")
def list_(ctx: typer.Context, org: str | None = OrgOption) -> None:
    """List teams."""
    cli: CliContext = ctx.obj
    cli.renderer.data([t.model_dump() for t in cli.team_service.list_teams(org)], title="Teams")


@app.command()
def get(ctx: typer.Context, slug: str, org: str | None = OrgOption) -> None:
    """Show one team."""
    cli: CliContext = ctx.obj
    cli.renderer.data(cli.team_service.get(slug, org).model_dump(), title="Team")


@app.command()
def members(ctx: typer.Context, slug: str, org: str | None = OrgOption) -> None:
    """List a team's members."""
    cli: CliContext = ctx.obj
    rows = [m.model_dump() for m in cli.team_service.members(slug, org)]
    cli.renderer.data(rows, title=f"Members of {slug}")


@app.command()
def permissions(ctx: typer.Context, slug: str, org: str | None = OrgOption) -> None:
    """Show which repositories a team can access, and at what level."""
    cli: CliContext = ctx.obj
    rows = [p.model_dump() for p in cli.team_service.permissions(slug, org)]
    cli.renderer.data(rows, title=f"Permissions of {slug}")
