"""stratos config get | set | list | reset."""

from enum import StrEnum
from pathlib import Path

import typer

from stratos.cli.context import CliContext
from stratos.config import loader
from stratos.domain.exceptions import ConfigurationError

app = typer.Typer(help="View and change Stratos configuration.", no_args_is_help=True)


class Scope(StrEnum):
    USER = "user"
    PROJECT = "project"


def _path(scope: Scope) -> Path:
    return loader.project_config_path() if scope is Scope.PROJECT else loader.user_config_path()


ScopeOption = typer.Option(Scope.USER, "--scope", help="Which config file to modify.")


@app.command("list")
def list_(ctx: typer.Context) -> None:
    """List all effective settings."""
    cli: CliContext = ctx.obj
    cli.renderer.data(cli.settings.to_flat(), title="Configuration")


@app.command()
def get(ctx: typer.Context, key: str) -> None:
    """Show one setting, e.g. `api.endpoint`."""
    cli: CliContext = ctx.obj
    flat = cli.settings.to_flat()
    if key not in flat:
        raise ConfigurationError(
            f"Unknown configuration key '{key}'.", hint="See `stratos config list`."
        )
    cli.renderer.data({key: flat[key]})


@app.command()
def set(ctx: typer.Context, key: str, value: str, scope: Scope = ScopeOption) -> None:
    """Set a value in the user (default) or project config file."""
    cli: CliContext = ctx.obj
    loader.set_value(_path(scope), key, value)
    cli.renderer.success(f"Set {key} in {scope.value} configuration.")


@app.command()
def reset(
    ctx: typer.Context,
    key: str | None = typer.Argument(None, help="Key to reset; omit to reset everything."),
    scope: Scope = ScopeOption,
) -> None:
    """Remove a key (or the whole file) from the chosen config file."""
    cli: CliContext = ctx.obj
    loader.reset_value(_path(scope), key)
    cli.renderer.success(f"Reset {key or 'all settings'} in {scope.value} configuration.")
