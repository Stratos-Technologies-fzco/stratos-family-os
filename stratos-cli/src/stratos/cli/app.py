"""Root `stratos` command: global options, lazy command groups, error handling."""

import os
from typing import Any

import typer
from typer.core import TyperCommand, TyperGroup

from stratos import __version__
from stratos.cli.context import CliContext
from stratos.cli.errors import handle_error
from stratos.cli.output import ConsoleRenderer
from stratos.domain.enums import ExitCode, OutputFormat
from stratos.domain.exceptions import OperationCancelledError
from stratos.logging import configure_logging, level_for

# name -> (module:attribute of the Typer app, one-line help shown by `stratos --help`).
# The help text is static so listing commands imports nothing; a test keeps it identical to
# each command's real help.
LAZY_COMMANDS: dict[str, tuple[str, str]] = {
    "agent": ("stratos.cli.commands.agent:app", "Define and run agents."),
    "ai": ("stratos.cli.commands.ai:app", "Ask AI models (provider-neutral)."),
    "audit": ("stratos.cli.commands.audit:app", "Review the audit trail of sensitive actions."),
    "auth": ("stratos.cli.commands.auth:app", "Sign in and manage your session."),
    "backup": (
        "stratos.cli.commands.backup:app",
        "List and restore backups of files Stratos changed.",
    ),
    "cache": ("stratos.cli.commands.cache:app", "Inspect and clear the local metadata cache."),
    "config": ("stratos.cli.commands.config:app", "View and change Stratos configuration."),
    "deploy": ("stratos.cli.commands.deploy:app", "Deploy, inspect and roll back releases."),
    "doctor": (
        "stratos.cli.commands.diagnostics:doctor_app",
        "Check this machine, project and configuration. Offline unless --online is given.",
    ),
    "environment": ("stratos.cli.commands.environment:app", "Manage a project's environments."),
    "init": (
        "stratos.cli.commands.project:init_app",
        "Bring this folder into the Stratos setup. Safe to run repeatedly.",
    ),
    "knowledge": ("stratos.cli.commands.knowledge:app", "Search organisational knowledge."),
    "login": (
        "stratos.cli.commands.auth:login_app",
        "Sign in through your organisation's identity provider.",
    ),
    "logout": ("stratos.cli.commands.auth:logout_app", "Sign out and remove stored credentials."),
    "mcp": ("stratos.cli.commands.mcp:app", "Manage MCP servers under organisation policy."),
    "org": ("stratos.cli.commands.org:app", "View organisation information."),
    "project": ("stratos.cli.commands.project:app", "Create and manage projects."),
    "repo": ("stratos.cli.commands.repo:app", "Create and manage GitHub repositories."),
    "skill": ("stratos.cli.commands.skill:app", "Discover and manage skills."),
    "status": (
        "stratos.cli.commands.diagnostics:status_app",
        "Summarise session, organisation, project, workspaces and environments (no network).",
    ),
    "team": ("stratos.cli.commands.team:app", "View teams and their access."),
    "version": (
        "stratos.cli.commands.diagnostics:version_app",
        "Show version, Python, platform, architecture and API version.",
    ),
    "whoami": ("stratos.cli.commands.auth:whoami_app", "Show the signed-in identity and roles."),
    "workspace": (
        "stratos.cli.commands.workspace:app",
        "Provision and connect developer workspaces.",
    ),
}
LAZY_GROUPS: dict[str, str] = {name: target for name, (target, _) in LAZY_COMMANDS.items()}


class LazyCommand(TyperCommand):
    """Stands in for a command in `--help`; the real command loads only when it is used."""

    def __init__(self, name: str, help_text: str, target: str) -> None:
        super().__init__(name=name, help=help_text)
        self._target = target

    def load(self) -> Any:
        module_name, attr = self._target.split(":")
        module = __import__(module_name, fromlist=[attr])
        command = typer.main.get_command(getattr(module, attr))
        command.name = self.name
        return command

    def make_context(self, info_name: Any, args: Any, parent: Any = None, **extra: Any) -> Any:
        return self.load().make_context(info_name, args, parent=parent, **extra)


class StratosGroup(TyperGroup):
    """Lists commands without importing them and maps every failure to a stable exit code."""

    def list_commands(self, ctx: Any) -> list[str]:
        return sorted(LAZY_COMMANDS)

    def get_command(self, ctx: Any, cmd_name: str) -> Any:
        entry = LAZY_COMMANDS.get(cmd_name)
        if entry is None:
            return None
        target, help_text = entry
        return LazyCommand(cmd_name, help_text, target)

    def invoke(self, ctx: Any) -> Any:
        try:
            return super().invoke(ctx)
        except typer.Abort:
            exc: BaseException = OperationCancelledError("Operation cancelled.")
        except Exception as caught:
            # typer.Exit and usage errors (which can `.show()` themselves) keep their own
            # output and exit codes (usage errors exit 2).
            if isinstance(caught, typer.Exit) or hasattr(caught, "show"):
                raise
            exc = caught
        cli_ctx = ctx.obj if isinstance(ctx.obj, CliContext) else None
        debug = cli_ctx.debug if cli_ctx else bool(os.environ.get("STRATOS_DEBUG"))
        renderer = cli_ctx.renderer if cli_ctx else ConsoleRenderer()
        code = handle_error(exc, renderer, debug=debug)
        raise typer.Exit(int(code))


app = typer.Typer(
    cls=StratosGroup,
    name="stratos",
    help="Stratos Enterprise Developer Platform CLI.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"stratos {__version__}")
        raise typer.Exit(int(ExitCode.SUCCESS))


@app.callback()
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Verbose (INFO) logging."),
    debug: bool = typer.Option(False, "--debug", help="Debug logging and tracebacks."),
    output: OutputFormat = typer.Option(
        OutputFormat.TABLE, "--output", "-o", help="Output format.", case_sensitive=False
    ),
) -> None:
    renderer = ConsoleRenderer(output)
    configure_logging(level_for(verbose, debug), as_json=output is OutputFormat.JSON)
    ctx.obj = CliContext(renderer=renderer, verbose=verbose, debug=debug)
