"""stratos audit list | get."""

import typer

from stratos.cli.context import CliContext
from stratos.domain.enums import AuditAction, Permission

app = typer.Typer(help="Review the audit trail of sensitive actions.", no_args_is_help=True)


@app.command("list")
def list_(
    ctx: typer.Context,
    action: AuditAction | None = typer.Option(None, "--action", help="Only this action."),
    user: str | None = typer.Option(None, "--user", help="Only this user."),
    limit: int = typer.Option(50, "--limit", min=1, max=1000, help="Maximum events, newest first."),
) -> None:
    """List audit events, newest first."""
    cli: CliContext = ctx.obj
    cli.require(Permission.AUDIT_READ)
    events = cli.audit.list_events(limit=limit, action=action, user=user)
    cli.renderer.data(
        [
            {
                "id": e.id,
                "time": e.timestamp.isoformat(timespec="seconds"),
                "user": e.user,
                "action": e.action.value,
                "resource": e.resource,
                "resource_id": e.resource_id or "",
                "result": e.result.value,
            }
            for e in events
        ],
        title="Audit events",
    )


@app.command()
def get(ctx: typer.Context, event_id: str) -> None:
    """Show one audit event in full."""
    cli: CliContext = ctx.obj
    cli.require(Permission.AUDIT_READ)
    cli.renderer.data(cli.audit.get_event(event_id).model_dump(mode="json"), title="Audit event")
