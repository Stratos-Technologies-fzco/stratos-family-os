"""Integration: several layers together through the CLI, with only the outside world faked."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stratos.cli.app import app

pytestmark = pytest.mark.integration
runner = CliRunner()


def test_init_is_repeatable_and_reversible(cli_sandbox: Callable[..., Path]) -> None:
    project = cli_sandbox("developer")
    first = runner.invoke(app, ["init", "--yes"])
    assert first.exit_code == 0, first.output
    settings = project / ".claude" / "settings.json"
    assert settings.exists()
    before = settings.read_text(encoding="utf-8")
    second = runner.invoke(app, ["init", "--yes"])
    assert second.exit_code == 0, second.output
    assert settings.read_text(encoding="utf-8") == before  # idempotent
    json.loads(before)


def test_audit_records_init(cli_sandbox: Callable[..., Path]) -> None:
    cli_sandbox("admin")
    assert runner.invoke(app, ["init", "--yes"]).exit_code == 0
    shown = runner.invoke(app, ["-o", "json", "audit", "list"])
    assert shown.exit_code == 0 and "project.init" in shown.output


def test_signed_out_user_cannot_initialise(cli_sandbox: Callable[..., Path]) -> None:
    project = cli_sandbox("viewer")
    assert runner.invoke(app, ["logout"]).exit_code == 0
    assert runner.invoke(app, ["init", "--yes"]).exit_code != 0
    assert not (project / ".claude").exists()
