"""Root `stratos` command: global options, lazy command groups, error handling."""

import os
from typing import Any

import typer
from typer.core import TyperGroup

from stratos import __version__
from stratos.cli.context import CliContext
from stratos.cli.errors import handle_error
from stratos.cli.output import ConsoleRenderer
from stratos.domain.enums import ExitCode, OutputFormat
from stratos.domain.exceptions import OperationCancelledError
from stratos.logging import configure_logging, level_for

# Command groups, imported only when invoked (keeps `stratos --help` fast and
# client-free). Later modules add entries here.
LAZY_GROUPS: dict[str, str] = {
    "agent": "stratos.cli.commands.agent:app",
    "ai": "stratos.cli.commands.ai:app",
    "audit": "stratos.cli.commands.audit:app",
    "auth": "stratos.cli.commands.auth:app",
    "config": "stratos.cli.commands.config:app",
    "doctor": "stratos.cli.commands.diagnostics:doctor_app",
    "knowledge": "stratos.cli.commands.knowledge:app",
    "login": "stratos.cli.commands.auth:login_app",
    "logout": "stratos.cli.commands.auth:logout_app",
    "mcp": "stratos.cli.commands.mcp:app",
    "org": "stratos.cli.commands.org:app",
    "repo": "stratos.cli.commands.repo:app",
    "skill": "stratos.cli.commands.skill:app",
    "status": "stratos.cli.commands.diagnostics:status_app",
    "team": "stratos.cli.commands.team:app",
    "version": "stratos.cli.commands.diagnostics:version_app",
    "whoami": "stratos.cli.commands.auth:whoami_app",
}


class StratosGroup(TyperGroup):
    """Loads groups lazily and maps every failure to a stable exit code."""

    def list_commands(self, ctx: Any) -> list[str]:
        return sorted(LAZY_GROUPS)

    def get_command(self, ctx: Any, cmd_name: str) -> Any:
        target = LAZY_GROUPS.get(cmd_name)
        if target is None:
            return None
        module_name, attr = target.split(":")
        module = __import__(module_name, fromlist=[attr])
        command = typer.main.get_command(getattr(module, attr))
        command.name = cmd_name
        return command

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
