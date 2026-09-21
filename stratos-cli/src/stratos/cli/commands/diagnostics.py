"""stratos doctor | version | status."""

import platform
import sys

import typer

from stratos import __version__
from stratos.application.diagnostics import run_checks, summarise
from stratos.cli.context import CliContext
from stratos.domain.exceptions import DependencyError

doctor_app = typer.Typer()
version_app = typer.Typer()
status_app = typer.Typer()

_SYMBOL = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}


@doctor_app.command("doctor")
def doctor(ctx: typer.Context) -> None:
    """Check this machine and configuration. Makes no network calls."""
    cli: CliContext = ctx.obj
    checks = run_checks(cli.diagnostics_facts())
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
    """Show version and runtime information."""
    cli: CliContext = ctx.obj
    cli.renderer.data(
        {
            "stratos": __version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "executable": sys.executable,
        },
        title="Version",
    )


@status_app.command("status")
def status(ctx: typer.Context) -> None:
    """Summarise session, organisation and project state (no network calls)."""
    cli: CliContext = ctx.obj
    s = cli.settings
    session = cli.auth_service.status().model_dump(mode="json") if s.auth.issuer else {}
    detected = cli.claude.detect()
    cli.renderer.data(
        {
            "signed_in": bool(session.get("authenticated")),
            "user": session.get("email") or session.get("subject") or "-",
            "organisation": s.github.organization,
            "api_endpoint": str(s.api.endpoint),
            "ai": f"{s.ai.provider} / {s.ai.model}",
            "claude_code": "detected" if detected.detected else "not detected",
            "project_dir": str(cli.project_dir),
            "skills_installed": ", ".join(cli.claude.installed_skill_names()) or "none",
            "knowledge_sources": ", ".join(cli.knowledge_source_labels()) or "none",
        },
        title="Status",
    )
