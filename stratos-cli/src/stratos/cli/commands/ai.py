"""stratos ai ask | models | status."""

import asyncio

import typer

from stratos.cli.context import CliContext
from stratos.domain.enums import OutputFormat

app = typer.Typer(help="Ask AI models (provider-neutral).", no_args_is_help=True)


@app.command()
def ask(
    ctx: typer.Context,
    prompt: str,
    model: str | None = typer.Option(None, "--model", help="Model to use (default: configured)."),
    system: str | None = typer.Option(None, "--system", help="System prompt."),
    no_stream: bool = typer.Option(False, "--no-stream", help="Wait for the full answer."),
) -> None:
    """Ask a question. The answer streams to the terminal unless a data format is selected."""
    cli: CliContext = ctx.obj
    svc = cli.ai_service
    r = cli.renderer
    streaming = not no_stream and r.format in (OutputFormat.TABLE, OutputFormat.PLAIN)

    async def run_stream() -> None:
        r.stream_begin()
        try:
            async for chunk in svc.stream(prompt, model=model, system=system):
                r.stream_write(chunk)
        finally:
            r.stream_end()

    if streaming:
        asyncio.run(run_stream())
    else:
        answer = asyncio.run(svc.ask(prompt, model=model, system=system))
        r.data(answer.model_dump(), title="Answer")


@app.command()
def models(ctx: typer.Context) -> None:
    """List the models the provider offers."""
    cli: CliContext = ctx.obj
    found = asyncio.run(cli.ai_service.models())
    cli.renderer.data([m.model_dump() for m in found], title="Models")


@app.command()
def status(ctx: typer.Context) -> None:
    """Show the configured provider and model (local check; no network)."""
    cli: CliContext = ctx.obj
    s = cli.ai_service.status()
    cli.renderer.data(
        {"provider": s.provider, "model": s.model, "api_key_configured": s.api_key_configured},
        title="AI",
    )
