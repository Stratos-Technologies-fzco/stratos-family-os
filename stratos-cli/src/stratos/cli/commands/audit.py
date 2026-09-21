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


@app.command()
def summary(
    ctx: typer.Context,
    days: int | None = typer.Option(
        None, "--days", min=1, max=365, help="Period (default: configured)."
    ),
) -> None:
    """Usage analytics and monitoring alerts over a period."""
    cli: CliContext = ctx.obj
    cli.require(Permission.AUDIT_READ)
    m = cli.settings.monitoring
    result = cli.audit.summary(
        days=days or m.window_days,
        denied_threshold=m.denied_threshold,
        failure_rate_threshold=m.failure_rate_threshold,
        min_events_for_rate=m.min_events_for_rate,
    )
    cli.renderer.data(
        {
            "since": result.since.isoformat(timespec="seconds") if result.since else "-",
            "events": result.total,
            "failure_rate": result.failure_rate,
            "by_result": ", ".join(f"{k}={v}" for k, v in sorted(result.by_result.items())) or "-",
            "by_action": ", ".join(f"{k}={v}" for k, v in sorted(result.by_action.items())) or "-",
            "by_user": ", ".join(f"{k}={v}" for k, v in sorted(result.by_user.items())) or "-",
        },
        title="Audit summary",
    )
    for alert in result.alerts:
        cli.renderer.warning(f"ALERT: {alert}")
