"""stratos workspace create | list | get | connect | delete."""

from pathlib import Path

import typer

from stratos.cli.context import CliContext
from stratos.cli.options import DryRun, Yes
from stratos.domain.models.workflow import WorkspaceRecord

app = typer.Typer(help="Provision and connect developer workspaces.", no_args_is_help=True)
OrgOption = typer.Option(None, "--org", help="GitHub organisation (default: configured).")


def _row(w: WorkspaceRecord) -> dict[str, object]:
    return {"name": w.name, "project": w.project, "path": w.path, "status": w.status}


@app.command()
def create(
    ctx: typer.Context,
    name: str,
    project: str = typer.Option(..., "--project", help="Registered project to work on."),
    path: Path | None = typer.Option(None, "--path", help="Folder (default: workspace root/NAME)."),
    python: bool = typer.Option(False, "--python", help="Create a Python virtual environment."),
    node: bool = typer.Option(False, "--node", help="Check Node.js is available."),
    hooks: bool = typer.Option(
        True, "--hooks/--no-hooks", help="Install the secret-blocking git hook."
    ),
    install_deps: bool = typer.Option(
        False, "--install-deps", help="Also install dependencies (runs third-party code)."
    ),
    skill: list[str] = typer.Option([], "--skill"),
    mcp: list[str] = typer.Option([], "--mcp"),
    org: str | None = OrgOption,
    dry_run: bool = DryRun,
    yes: bool = Yes,
) -> None:
    """Create or reconcile a workspace (safe to re-run)."""
    cli: CliContext = ctx.obj
    results = cli.workspace_service(dry_run=dry_run, yes=yes).create(
        name, project, path=path, python=python, node=node, hooks=hooks,
        install_deps=install_deps, skills=tuple(skill), mcp=tuple(mcp), org=org,
    )  # fmt: skip
    if results is None:
        return
    cli.renderer.data(
        [{"component": r.name, "result": r.status, "detail": r.detail} for r in results],
        title=f"Workspace {name}",
    )
    cli.renderer.success(f"Workspace '{name}' is ready. Use `stratos workspace connect {name}`.")


@app.command("list")
def list_(ctx: typer.Context) -> None:
    """List workspaces."""
    cli: CliContext = ctx.obj
    cli.renderer.data(
        [_row(w) for w in cli.workspace_service().list_workspaces()], title="Workspaces"
    )


@app.command()
def get(ctx: typer.Context, name: str) -> None:
    """Show one workspace."""
    cli: CliContext = ctx.obj
    data = cli.workspace_service().get(name).model_dump(mode="json")
    data["components"] = ", ".join(data["components"]) or "-"
    cli.renderer.data(data, title=name)


@app.command()
def connect(
    ctx: typer.Context,
    name: str,
    path: Path | None = typer.Option(
        None, "--path", help="Adopt an existing clone at this folder."
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Re-apply the workspace setup."),
) -> None:
    """Show how to enter a workspace (adopting or refreshing it if asked)."""
    cli: CliContext = ctx.obj
    info = cli.workspace_service().connect(name, path=path, refresh=refresh)
    cli.renderer.data(
        {
            "workspace": info.record.name,
            "project": info.record.project,
            "path": info.record.path,
            "enter": info.enter,
            "activate": info.activate or "-",
            "problems": "; ".join(info.problems) or "none",
        },
        title="Workspace",
    )
    if info.problems:
        for problem in info.problems:
            cli.renderer.warning(problem)
    else:
        cli.renderer.success(f"Run: {info.enter}")


@app.command()
def delete(
    ctx: typer.Context,
    name: str,
    purge_files: bool = typer.Option(False, "--purge-files", help="Also delete the folder."),
    force: bool = typer.Option(False, "--force", help="Delete even with uncommitted changes."),
    dry_run: bool = DryRun,
    yes: bool = Yes,
) -> None:
    """Remove a workspace from the registry (its folder is kept unless --purge-files)."""
    cli: CliContext = ctx.obj
    record = cli.workspace_service(dry_run=dry_run, yes=yes).delete(
        name, purge_files=purge_files, force=force
    )
    if record is not None:
        cli.renderer.success(f"Workspace '{name}' removed.")
