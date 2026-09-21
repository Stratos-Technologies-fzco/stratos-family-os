"""stratos doctor | version | status."""

import platform
import sys

import typer

from stratos import __version__
from stratos.application.diagnostics import run_checks, summarise
from stratos.cli.context import CliContext
from stratos.domain.exceptions import ConfigurationError, DependencyError

doctor_app = typer.Typer()
version_app = typer.Typer()
status_app = typer.Typer()

_SYMBOL = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}


@doctor_app.command("doctor")
def doctor(
    ctx: typer.Context,
    online: bool = typer.Option(
        False,
        "--online",
        help="Also test the network, GitHub and the Platform API (no credentials sent).",
    ),
) -> None:
    """Check this machine, project and configuration. Offline unless --online is given."""
    cli: CliContext = ctx.obj
    checks = run_checks(cli.diagnostics_facts(online=online))
    cli.renderer.data(
        [{"check": c.name, "result": _SYMBOL[c.level], "detail": c.detail} for c in checks],
        title="Stratos doctor",
    )
    passed, warnings, failures = summarise(checks)
    summary = f"{passed} passed, {warnings} warnings, {failures} failed."
    if failures:
        raise DependencyError(f"Doctor found problems: {summary}")
    cli.renderer.success(summary)


@version_app.command("version")
def version(ctx: typer.Context) -> None:
    """Show version, Python, platform, architecture and API version."""
    cli: CliContext = ctx.obj
    try:
        endpoint = str(cli.settings.api.endpoint)
    except ConfigurationError:
        endpoint = "-"
    cli.renderer.data(
        {
            "stratos": __version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "api_endpoint": endpoint,
            "api_version": "not published (the Platform API has no versioned contract yet)",
            "executable": sys.executable,
        },
        title="Version",
    )


@status_app.command("status")
def status(ctx: typer.Context) -> None:
    """Summarise session, organisation, project, workspaces and environments (no network)."""
    from stratos.application.init_service import read_manifest

    cli: CliContext = ctx.obj
    s = cli.settings
    session = cli.auth_service.status().model_dump(mode="json") if s.auth.issuer else {}
    detected = cli.claude.detect()
    manifest = None
    try:
        manifest = read_manifest(cli.project_dir)
    except ConfigurationError:
        pass
    workspaces = cli.workspace_store.list()
    known_projects = cli.project_store.list(cli.default_org)
    cli.renderer.data(
        {
            "signed_in": bool(session.get("authenticated")),
            "user": session.get("email") or session.get("subject") or "-",
            "organisation": s.github.organization,
            "api_endpoint": str(s.api.endpoint),
            "ai": f"{s.ai.provider} / {s.ai.model}",
            "claude_code": "detected" if detected.detected else "not detected",
            "project_dir": str(cli.project_dir),
            "project": manifest.name if manifest else "not a Stratos project (run `stratos init`)",
            "environments": ", ".join(manifest.environments)
            if manifest and manifest.environments
            else "-",
            "registered_projects": len(known_projects),
            "workspaces": len(workspaces),
            "skills_installed": ", ".join(cli.claude.installed_skill_names()) or "none",
            "knowledge_sources": ", ".join(cli.knowledge_source_labels()) or "none",
        },
        title="Status",
    )
