"""stratos mcp list | install | configure | status."""

import typer

from stratos.cli.context import CliContext
from stratos.cli.options import DryRun, Yes

app = typer.Typer(help="Manage MCP servers under organisation policy.", no_args_is_help=True)


@app.command("list")
def list_(ctx: typer.Context) -> None:
    """List registry servers, whether installed, and whether policy allows them."""
    cli: CliContext = ctx.obj
    rows = [
        {
            "name": r.name,
            "installed": r.installed,
            "allowed": r.allowed,
            "description": r.description,
        }
        for r in cli.mcp_service().list_servers()
    ]
    cli.renderer.data(rows, title="MCP servers")


@app.command()
def install(ctx: typer.Context, name: str, dry_run: bool = DryRun, yes: bool = Yes) -> None:
    """Install a server into .mcp.json (existing configuration is backed up first)."""
    cli: CliContext = ctx.obj
    result = cli.mcp_service(dry_run=dry_run, yes=yes).install(name)
    if result is not None:
        cli.renderer.success(
            f"MCP server '{name}' " + ("installed." if result.changed else "was already installed.")
        )
        if result.backup:
            cli.renderer.info(f"Previous configuration backed up to {result.backup}")


@app.command()
def configure(
    ctx: typer.Context,
    name: str,
    env: list[str] = typer.Option(
        [], "--env", help="KEY=VARIABLE: read KEY from an environment variable (never a value)."
    ),
    arg: list[str] = typer.Option([], "--arg", help="Replace the server's arguments."),
    dry_run: bool = DryRun,
) -> None:
    """Change an installed server's environment references or arguments."""
    cli: CliContext = ctx.obj
    mapping = dict(item.split("=", 1) for item in env if "=" in item)
    result = cli.mcp_service(dry_run=dry_run).configure(name, env=mapping or None, args=arg or None)
    if result is not None:
        cli.renderer.success(
            f"MCP server '{name}' " + ("updated." if result.changed else "unchanged.")
        )


@app.command()
def status(ctx: typer.Context) -> None:
    """Show installed servers, policy compliance and missing environment variables."""
    cli: CliContext = ctx.obj
    rows = [
        {
            "name": r.name,
            "in_registry": r.in_registry,
            "allowed": r.allowed,
            "missing_env": ", ".join(r.missing_env) or "none",
        }
        for r in cli.mcp_service().status()
    ]
    cli.renderer.data(rows, title="MCP status")
