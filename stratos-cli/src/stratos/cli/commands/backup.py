"""stratos backup list | restore."""

import typer

from stratos.cli.context import CliContext
from stratos.cli.options import DryRun, Yes
from stratos.domain.models.files import BackupEntry, WriteResult

app = typer.Typer(help="List and restore backups of files Stratos changed.", no_args_is_help=True)


@app.command("list")
def list_(
    ctx: typer.Context,
    file: str | None = typer.Option(
        None, "--file", help="Only backups of files containing this text."
    ),
) -> None:
    """List backups taken before Stratos changed a file, newest first."""
    cli: CliContext = ctx.obj
    entries = cli.backup_service().list_backups(file=file)
    if not entries:
        cli.renderer.info("No backups found in this project.")
        return
    cli.renderer.data(
        [
            {
                "id": e.id,
                "restores": e.original.name,
                "kind": e.kind,
                "created": e.created,
                "bytes": e.size,
            }
            for e in entries
        ],
        title="Backups",
    )


@app.command()
def restore(
    ctx: typer.Context,
    backup: str = typer.Argument(..., help="Backup id or file name (see `backup list`)."),
    dry_run: bool = DryRun,
    yes: bool = Yes,
) -> None:
    """Put a backed-up version back. The current version is backed up first."""
    cli: CliContext = ctx.obj
    result = cli.backup_service(dry_run=dry_run, yes=yes).restore(backup)
    if result is None:
        return
    target = result.path if isinstance(result, WriteResult) else result.original
    changed = result.changed if isinstance(result, WriteResult) else True
    assert isinstance(result, WriteResult | BackupEntry)
    cli.renderer.success(
        f"Restored {target.name}." if changed else f"{target.name} already matches."
    )
