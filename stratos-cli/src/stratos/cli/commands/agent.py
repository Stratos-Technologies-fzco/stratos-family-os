"""stratos agent list | get | run | create | logs."""

import asyncio
import sys
from pathlib import Path

import typer

from stratos.cli.context import CliContext
from stratos.cli.options import DryRun, Yes
from stratos.domain.enums import AgentPermission
from stratos.domain.exceptions import ValidationError
from stratos.domain.models.extensions import AgentDefinition
from stratos.utils.files import read_text_limited

app = typer.Typer(help="Define and run agents.", no_args_is_help=True)


@app.command("list")
def list_(ctx: typer.Context) -> None:
    """List built-in and project agents."""
    cli: CliContext = ctx.obj
    rows = [
        {
            "name": a.name,
            "description": a.description,
            "permissions": ", ".join(p.value for p in a.permissions) or "none",
        }
        for a in cli.agent_service().list_agents()
    ]
    cli.renderer.data(rows, title="Agents")


@app.command()
def get(ctx: typer.Context, name: str) -> None:
    """Show an agent's definition."""
    cli: CliContext = ctx.obj
    cli.renderer.data(cli.agent_service().get(name).model_dump(mode="json"), title=name)


@app.command()
def run(
    ctx: typer.Context,
    name: str,
    input_file: Path | None = typer.Option(
        None, "--input", help="File to work on (code, diff, doc)."
    ),
    text: str | None = typer.Option(None, "--text", help="Text to work on."),
) -> None:
    """Run an agent on a file, text, or standard input."""
    cli: CliContext = ctx.obj
    if input_file:
        task = read_text_limited(input_file)
    elif text:
        task = text
    elif not sys.stdin.isatty():
        task = sys.stdin.read(100_001)
    else:
        raise ValidationError(
            "Nothing to run on.", hint="Use --input FILE, --text TEXT or pipe input."
        )
    with cli.renderer.status(f"Running {name}..."):
        record = asyncio.run(cli.agent_service().run(name, task))
    cli.renderer.data(record.model_dump(mode="json"), title=f"Run {record.run_id}")


@app.command()
def create(
    ctx: typer.Context,
    name: str,
    instructions_file: Path = typer.Option(..., "--instructions-file", help="Agent instructions."),
    description: str = typer.Option("", "--description"),
    model: str | None = typer.Option(None, "--model"),
    tool: list[str] = typer.Option([], "--tool", help="Tools (available: knowledge.search)."),
    permission: list[AgentPermission] = typer.Option([], "--permission"),
    knowledge: list[str] = typer.Option([], "--knowledge"),
    skill: list[str] = typer.Option([], "--skill", help="Recorded; not executed yet."),
    mcp: list[str] = typer.Option([], "--mcp", help="Recorded; not executed yet."),
    dry_run: bool = DryRun,
    yes: bool = Yes,
) -> None:
    """Register a project agent in .stratos/agents/."""
    cli: CliContext = ctx.obj
    agent = AgentDefinition(
        name=name,
        description=description,
        instructions=read_text_limited(instructions_file, 20_000),
        model=model,
        tools=tuple(tool),
        skills=tuple(skill),
        mcp_servers=tuple(mcp),
        knowledge_sources=tuple(knowledge),
        permissions=tuple(permission),
    )
    path = cli.agent_service(dry_run=dry_run, yes=yes).create(agent)
    if path:
        cli.renderer.success(f"Agent '{name}' registered: {path}")


@app.command()
def logs(
    ctx: typer.Context,
    name: str,
    limit: int = typer.Option(10, "--limit", min=1, max=100),
) -> None:
    """Show recent runs of an agent (logs are redacted)."""
    cli: CliContext = ctx.obj
    records = cli.agent_service().logs(name, limit=limit)
    if not records:
        cli.renderer.info(f"No runs recorded for '{name}'.")
        return
    cli.renderer.data([r.model_dump(mode="json") for r in records], title=f"Runs of {name}")


@app.command()
def sync(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Agent to export; omit for all."),
    dry_run: bool = DryRun,
) -> None:
    """Export agents to .claude/agents as Claude Code subagents (never overwrites your own)."""
    cli: CliContext = ctx.obj
    results = cli.claude_service(dry_run=dry_run).sync_agents(name)
    for agent_name, result in results or []:
        state = "written" if result.changed else "already up to date"
        cli.renderer.success(f".claude/agents/{agent_name}.md {state}")
