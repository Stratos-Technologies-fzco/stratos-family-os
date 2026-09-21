"""stratos cache status | prune | clear."""

import typer

from stratos.cli.context import CliContext

app = typer.Typer(help="Inspect and clear the local metadata cache.", no_args_is_help=True)


@app.command()
def status(ctx: typer.Context) -> None:
    """Show cached datasets, their size, and how many entries have expired."""
    cli: CliContext = ctx.obj
    stats = cli.cache.stats()
    if not stats:
        cli.renderer.info("The cache is empty.")
        return
    cli.renderer.data(
        [
            {
                "namespace": s.namespace,
                "entries": s.entries,
                "expired": s.expired,
                "bytes": s.size_bytes,
            }
            for s in stats
        ],
        title="Cache",
    )


@app.command()
def prune(ctx: typer.Context) -> None:
    """Remove expired entries."""
    cli: CliContext = ctx.obj
    removed = cli.cache.prune()
    cli.renderer.success(f"Removed {removed} expired entr{'y' if removed == 1 else 'ies'}.")


@app.command()
def clear(
    ctx: typer.Context,
    namespace: str | None = typer.Option(None, "--namespace", help="Only this dataset."),
) -> None:
    """Delete cached metadata (it is refetched on demand; nothing is lost)."""
    cli: CliContext = ctx.obj
    removed = cli.cache.clear(namespace)
    cli.renderer.success(f"Cleared {removed} cached entr{'y' if removed == 1 else 'ies'}.")
