"""Options shared by mutating commands (operation safety)."""

import typer

DryRun = typer.Option(False, "--dry-run", help="Show what would happen; change nothing.")
Yes = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts (for automation).")
