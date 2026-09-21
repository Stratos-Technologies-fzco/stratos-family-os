"""stratos repo create | list | clone | get | configure | archive."""

from pathlib import Path

import typer
import yaml

from stratos.cli.context import CliContext
from stratos.cli.options import DryRun, Yes
from stratos.domain.exceptions import ValidationError
from stratos.domain.models.github import Label, Repository
from stratos.utils.files import read_text_limited

app = typer.Typer(help="Create and manage GitHub repositories.", no_args_is_help=True)
OrgOption = typer.Option(None, "--org", help="GitHub organisation (default: configured).")


def _row(r: Repository) -> dict[str, object]:
    return {
        "name": r.full_name,
        "visibility": "private" if r.private else "public",
        "default_branch": r.default_branch,
        "archived": r.archived,
        "url": r.url,
    }


@app.command()
def create(
    ctx: typer.Context,
    name: str,
    public: bool = typer.Option(False, "--public", help="Create a public repository."),
    description: str | None = typer.Option(None, "--description"),
    org: str | None = OrgOption,
    dry_run: bool = DryRun,
    yes: bool = Yes,
) -> None:
    """Create a repository (safe to re-run: an existing one is left unchanged)."""
    cli: CliContext = ctx.obj
    result = cli.repository_service(dry_run=dry_run, yes=yes).create(
        name, private=not public, description=description, org=org
    )
    if result is None:
        return
    verb = "Created" if result.created else "Already exists:"
    cli.renderer.success(f"{verb} {result.repository.full_name}")
    cli.renderer.data(_row(result.repository))


@app.command("list")
def list_(
    ctx: typer.Context,
    org: str | None = OrgOption,
    limit: int = typer.Option(100, "--limit", min=1, max=1000),
) -> None:
    """List the organisation's repositories."""
    cli: CliContext = ctx.obj
    repos = cli.repository_service().list_repositories(org=org, limit=limit)
    cli.renderer.data([_row(r) for r in repos], title="Repositories")


@app.command()
def get(ctx: typer.Context, name: str, org: str | None = OrgOption) -> None:
    """Show one repository."""
    cli: CliContext = ctx.obj
    cli.renderer.data(_row(cli.repository_service().get(name, org=org)))


@app.command()
def clone(
    ctx: typer.Context,
    name: str,
    dest: Path | None = typer.Option(None, "--dest", help="Destination folder."),
    org: str | None = OrgOption,
) -> None:
    """Clone a repository with your own git credentials."""
    cli: CliContext = ctx.obj
    with cli.renderer.status(f"Cloning {name}..."):
        path = cli.repository_service().clone(name, dest, org=org)
    cli.renderer.success(f"Cloned into {path}")


def _load_labels(path: Path) -> list[Label]:
    try:
        raw = yaml.safe_load(read_text_limited(path))
        return [
            Label(
                name=str(d["name"]),
                color=str(d.get("color", "ededed")).lstrip("#").lower(),
                description=str(d.get("description", "")),
            )
            for d in raw
        ]
    except (yaml.YAMLError, KeyError, TypeError, ValueError) as exc:
        raise ValidationError(
            f"'{path.name}' must be a list of labels with a name (and optional color, description)."
        ) from exc


@app.command()
def configure(
    ctx: typer.Context,
    name: str,
    protect: bool = typer.Option(
        True, "--protect/--no-protect", help="Protect the default branch."
    ),
    codeowners: Path | None = typer.Option(None, "--codeowners", help="CODEOWNERS file to apply."),
    labels: Path | None = typer.Option(None, "--labels", help="YAML/JSON list of labels to sync."),
    policy: bool = typer.Option(False, "--policy", help="Apply the standard merge policy."),
    security: bool = typer.Option(False, "--security", help="Enable security scanning features."),
    actions: bool = typer.Option(False, "--actions", help="Restrict GitHub Actions."),
    org: str | None = OrgOption,
    dry_run: bool = DryRun,
) -> None:
    """Apply branch protection, CODEOWNERS, labels, merge policy, security and Actions settings."""
    cli: CliContext = ctx.obj
    done = cli.repository_service(dry_run=dry_run).configure(
        name,
        branch_protection=protect,
        codeowners=read_text_limited(codeowners) if codeowners else None,
        labels=_load_labels(labels) if labels else None,
        policy=policy,
        security=security,
        actions=actions,
        org=org,
    )
    for item in done:
        cli.renderer.success(item[0].upper() + item[1:])


StateOption = typer.Option("open", "--state", help="open, closed or all.")


@app.command()
def branches(ctx: typer.Context, name: str, org: str | None = OrgOption) -> None:
    """List branches and whether they are protected."""
    cli: CliContext = ctx.obj
    rows = [b.model_dump() for b in cli.repository_service().branches(name, org=org)]
    cli.renderer.data(rows, title="Branches")


@app.command()
def prs(
    ctx: typer.Context,
    name: str,
    state: str = StateOption,
    limit: int = typer.Option(50, "--limit", min=1, max=500),
    org: str | None = OrgOption,
) -> None:
    """List pull requests."""
    cli: CliContext = ctx.obj
    found = cli.repository_service().pull_requests(name, state=state, limit=limit, org=org)
    cli.renderer.data([p.model_dump() for p in found], title="Pull requests")


@app.command()
def issues(
    ctx: typer.Context,
    name: str,
    state: str = StateOption,
    limit: int = typer.Option(50, "--limit", min=1, max=500),
    org: str | None = OrgOption,
) -> None:
    """List issues (pull requests excluded)."""
    cli: CliContext = ctx.obj
    found = cli.repository_service().issues(name, state=state, limit=limit, org=org)
    cli.renderer.data(
        [{**i.model_dump(), "labels": ", ".join(i.labels)} for i in found], title="Issues"
    )


@app.command()
def labels(ctx: typer.Context, name: str, org: str | None = OrgOption) -> None:
    """List labels."""
    cli: CliContext = ctx.obj
    cli.renderer.data(
        [lb.model_dump() for lb in cli.repository_service().labels(name, org=org)], title="Labels"
    )


@app.command()
def workflows(ctx: typer.Context, name: str, org: str | None = OrgOption) -> None:
    """List GitHub Actions workflows."""
    cli: CliContext = ctx.obj
    rows = [w.model_dump() for w in cli.repository_service().workflows(name, org=org)]
    cli.renderer.data(rows, title="Workflows")


@app.command()
def runs(
    ctx: typer.Context,
    name: str,
    limit: int = typer.Option(20, "--limit", min=1, max=100),
    org: str | None = OrgOption,
) -> None:
    """List recent workflow runs."""
    cli: CliContext = ctx.obj
    rows = [
        r.model_dump() for r in cli.repository_service().workflow_runs(name, limit=limit, org=org)
    ]
    cli.renderer.data(rows, title="Workflow runs")


@app.command()
def archive(
    ctx: typer.Context,
    name: str,
    org: str | None = OrgOption,
    dry_run: bool = DryRun,
    yes: bool = Yes,
) -> None:
    """Archive a repository (makes it read-only). Asks for confirmation."""
    cli: CliContext = ctx.obj
    repo = cli.repository_service(dry_run=dry_run, yes=yes).archive(name, org=org)
    if repo is not None:
        cli.renderer.success(f"{repo.full_name} is archived.")
