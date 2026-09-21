"""stratos login | logout | whoami | auth login | logout | status | token."""

import sys

import typer

from stratos.cli.context import CliContext

app = typer.Typer(help="Sign in and manage your session.", no_args_is_help=True)
login_app = typer.Typer()
logout_app = typer.Typer()
whoami_app = typer.Typer()

NoBrowser = typer.Option(False, "--no-browser", help="Print the sign-in URL instead of opening it.")


def _login(ctx: typer.Context, no_browser: bool) -> None:
    cli: CliContext = ctx.obj
    svc = cli.auth_service

    def show(url: str) -> None:
        cli.renderer.info("Complete sign-in in your browser. If it does not open, visit:")
        cli.renderer.info(url)

    with cli.renderer.status("Waiting for browser sign-in..."):
        identity = svc.login(on_url=show, open_browser=not no_browser)
    cli.renderer.success(f"Signed in as {identity.display}.")


def _logout(ctx: typer.Context) -> None:
    cli: CliContext = ctx.obj
    if cli.auth_service.logout():
        cli.renderer.success("Signed out. Stored credentials were removed.")
    else:
        cli.renderer.info("You were not signed in.")


def _whoami(ctx: typer.Context) -> None:
    cli: CliContext = ctx.obj
    identity = cli.auth_service.whoami()
    data = identity.model_dump(mode="json")
    data["roles"] = ", ".join(sorted(r.value for r in cli.authorization.roles_for(identity)))
    cli.renderer.data(data, title="Identity")


@login_app.command("login")
def login(ctx: typer.Context, no_browser: bool = NoBrowser) -> None:
    """Sign in through your organisation's identity provider."""
    _login(ctx, no_browser)


@logout_app.command("logout")
def logout(ctx: typer.Context) -> None:
    """Sign out and remove stored credentials."""
    _logout(ctx)


@whoami_app.command("whoami")
def whoami(ctx: typer.Context) -> None:
    """Show the signed-in identity and roles."""
    _whoami(ctx)


@app.command("login")
def auth_login(ctx: typer.Context, no_browser: bool = NoBrowser) -> None:
    """Sign in through your organisation's identity provider."""
    _login(ctx, no_browser)


@app.command("logout")
def auth_logout(ctx: typer.Context) -> None:
    """Sign out and remove stored credentials."""
    _logout(ctx)


@app.command("status")
def status(ctx: typer.Context) -> None:
    """Show session status (local check; tokens are never displayed)."""
    cli: CliContext = ctx.obj
    cli.renderer.data(cli.auth_service.status().model_dump(mode="json"), title="Session")


@app.command("token")
def token(
    ctx: typer.Context,
    reveal: bool = typer.Option(
        False, "--reveal", help="Print the full access token (for scripts)."
    ),
) -> None:
    """Print the access token (hidden unless --reveal is given)."""
    cli: CliContext = ctx.obj
    value = cli.auth_service.access_token()
    if reveal:
        print(
            "Warning: this token grants access to your account; do not share it.", file=sys.stderr
        )
        cli.renderer.secret(value)
    else:
        cli.renderer.info("A valid token is available. It is hidden; use --reveal to print it.")
