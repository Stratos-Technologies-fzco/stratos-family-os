"""stratos knowledge search | get | sync | status | connect."""

import asyncio
from collections.abc import Awaitable, Callable

import typer

from stratos.application.knowledge_service import KnowledgeService
from stratos.cli.context import CliContext
from stratos.cli.options import DryRun

app = typer.Typer(help="Search organisational knowledge.", no_args_is_help=True)


def _run[T](cli: CliContext, action: Callable[[KnowledgeService], Awaitable[T]]) -> T:
    """Run an async knowledge call and always release network clients afterwards."""

    async def go() -> T:
        service = cli.knowledge_service
        try:
            return await action(service)
        finally:
            await service.aclose()

    return asyncio.run(go())


@app.command()
def search(
    ctx: typer.Context,
    query: str,
    limit: int = typer.Option(5, "--limit", min=1, max=50),
) -> None:
    """Search documents; results are ranked by relevance."""
    cli: CliContext = ctx.obj
    hits = _run(cli, lambda svc: svc.search(query, limit=limit))
    if not hits:
        cli.renderer.info("No matching documents.")
        return
    cli.renderer.data([h.model_dump() for h in hits], title="Results")


@app.command()
def get(ctx: typer.Context, doc_id: str) -> None:
    """Show one document by its id (as listed by `search`)."""
    cli: CliContext = ctx.obj
    doc = _run(cli, lambda svc: svc.get(doc_id))
    cli.renderer.data(doc.model_dump(), title=doc.title)


@app.command()
def sync(ctx: typer.Context) -> None:
    """Refresh the knowledge manifest."""
    cli: CliContext = ctx.obj
    for status in _run(cli, lambda svc: svc.sync()):
        detail = f" ({status.detail})" if status.detail else ""
        cli.renderer.success(f"{status.provider}: {status.documents} documents{detail}.")


@app.command()
def status(ctx: typer.Context) -> None:
    """Show knowledge sources and whether they changed since the last sync."""
    cli: CliContext = ctx.obj
    rows = [s.model_dump() for s in _run(cli, lambda svc: svc.status())]
    cli.renderer.data(rows, title="Knowledge")


@app.command()
def connect(ctx: typer.Context, dry_run: bool = DryRun) -> None:
    """Tell Claude Code how to use Stratos knowledge (managed block in CLAUDE.md)."""
    cli: CliContext = ctx.obj
    result = cli.claude_service(dry_run=dry_run).connect_knowledge(cli.knowledge_source_labels())
    if result is not None:
        cli.renderer.success("CLAUDE.md now explains how to use Stratos knowledge.")
