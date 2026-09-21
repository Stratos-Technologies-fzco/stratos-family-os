"""stratos skill list | install | update | remove."""

import typer

from stratos.cli.context import CliContext
from stratos.cli.options import DryRun, Yes

app = typer.Typer(help="Discover and manage skills.", no_args_is_help=True)


@app.command("list")
def list_(ctx: typer.Context) -> None:
    """List available skills and what is installed."""
    cli: CliContext = ctx.obj
    rows = [
        {
            "name": r.manifest.name,
            "version": r.manifest.version,
            "installed": r.installed_version or "-",
            "permissions": ", ".join(r.manifest.permissions) or "none",
            "description": r.manifest.description,
        }
        for r in cli.skill_service().list_skills()
    ]
    cli.renderer.data(rows, title="Skills")


@app.command()
def install(ctx: typer.Context, name: str, dry_run: bool = DryRun, yes: bool = Yes) -> None:
    """Verify (checksum, compatibility, permissions) and install a skill."""
    cli: CliContext = ctx.obj
    outcome = cli.skill_service(dry_run=dry_run, yes=yes).install(name)
    if outcome:
        cli.renderer.success(f"Skill '{outcome.name}' {outcome.version}: {outcome.action}.")


@app.command()
def update(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Skill to update; omit to update all."),
    dry_run: bool = DryRun,
    yes: bool = Yes,
) -> None:
    """Update installed skills to newer registry versions."""
    cli: CliContext = ctx.obj
    for outcome in cli.skill_service(dry_run=dry_run, yes=yes).update(name):
        cli.renderer.success(f"Skill '{outcome.name}' {outcome.version}: {outcome.action}.")


@app.command()
def remove(ctx: typer.Context, name: str, dry_run: bool = DryRun, yes: bool = Yes) -> None:
    """Remove an installed skill (a backup is kept)."""
    cli: CliContext = ctx.obj
    backup = cli.skill_service(dry_run=dry_run, yes=yes).remove(name)
    if not dry_run:
        cli.renderer.success(f"Removed '{name}'." + (f" Backup: {backup}" if backup else ""))
