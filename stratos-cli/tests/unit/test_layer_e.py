"""Layer E: lazy CLI start-up (M25), cache administration, backups and transactions (M24)."""

import subprocess
import sys
from pathlib import Path

import pytest
from test_layer_c import make_audit, make_guard, make_require
from typer.testing import CliRunner

from stratos.application.backup_service import BackupService
from stratos.application.transaction import Transaction
from stratos.cli.app import LAZY_COMMANDS, app
from stratos.domain.exceptions import AuthorizationError, ResourceNotFoundError
from stratos.infrastructure.filesystem.backups import LocalBackupCatalog
from stratos.infrastructure.filesystem.cache import TtlCache
from stratos.infrastructure.filesystem.config_files import ConfigFileProtector

runner = CliRunner()


# ---- lazy start-up -------------------------------------------------------------------------------
def test_static_help_matches_real_command_help() -> None:
    """The static `--help` table must not drift from what each command really declares."""
    import typer

    from stratos.cli.app import LazyCommand

    for name, (target, help_text) in LAZY_COMMANDS.items():
        command = LazyCommand(name, help_text, target).load()
        real = (command.help or "").strip().split("\n")[0]
        assert real == help_text, f"{name}: static help is out of date"
    assert typer  # imported for side effect of typer.main


def test_root_help_imports_no_command_module() -> None:
    code = (
        "import sys\n"
        "from typer.testing import CliRunner\n"
        "from stratos.cli.app import app\n"
        "r = CliRunner().invoke(app, ['--help'])\n"
        "assert r.exit_code == 0, r.output\n"
        "loaded = [m for m in sys.modules if m.startswith('stratos.cli.commands.')]\n"
        "assert not loaded, loaded\n"
        "assert 'httpx' not in sys.modules\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout


def test_root_help_lists_every_command() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in LAZY_COMMANDS:
        assert name in result.output


def test_unknown_command_is_a_usage_error() -> None:
    assert runner.invoke(app, ["nonsense"]).exit_code == 2


# ---- cache -----------------------------------------------------------------------------------------------
def test_cache_stats_prune_clear(tmp_path: Path) -> None:
    now = [1000.0]
    cache = TtlCache(tmp_path, clock=lambda: now[0])
    cache.set("org", "a", {"x": 1}, 10)
    cache.set("org", "b", {"x": 2}, 100)
    cache.set("skills", "c", [1], 100)
    now[0] = 1050.0
    stats = {s.namespace: s for s in cache.stats()}
    assert stats["org"].entries == 2 and stats["org"].expired == 1
    assert cache.prune() == 1
    assert cache.get("org", "b") == {"x": 2}
    assert cache.clear("org") == 1
    assert [s.namespace for s in cache.stats()] == ["skills"]
    assert cache.clear() == 1
    assert cache.stats() == []


def test_cache_commands(cli_sandbox) -> None:  # type: ignore[no-untyped-def]
    cli_sandbox("viewer")
    assert "empty" in runner.invoke(app, ["cache", "status"]).output.lower()
    from stratos.infrastructure.filesystem import cache as cache_module

    TtlCache(Path(cache_module.user_cache_dir())).set("org", "k", {"v": 1}, 60)
    assert "org" in runner.invoke(app, ["cache", "status"]).output
    assert runner.invoke(app, ["cache", "prune"]).exit_code == 0
    assert runner.invoke(app, ["cache", "clear", "--namespace", "org"]).exit_code == 0


# ---- transaction ------------------------------------------------------------------------------------------------
def test_transaction_rolls_back_writes_and_runs_undo(tmp_path: Path) -> None:
    protector = ConfigFileProtector()
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    undone: list[str] = []
    with pytest.raises(RuntimeError), Transaction(protector) as tx:
        tx.record(protector.write_text(target, "new"))
        tx.record(protector.write_text(tmp_path / "b.txt", "fresh"))
        tx.on_rollback(lambda: undone.append("x"))
        raise RuntimeError("boom")
    assert target.read_text(encoding="utf-8") == "old"
    assert not (tmp_path / "b.txt").exists()
    assert undone == ["x"]


def test_transaction_keeps_writes_on_success(tmp_path: Path) -> None:
    protector = ConfigFileProtector()
    with Transaction(protector) as tx:
        tx.record(protector.write_text(tmp_path / "a.txt", "new"))
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "new"
    assert len(tx.writes) == 1


# ---- backups ------------------------------------------------------------------------------------------------------------
def _service(tmp_path: Path, *roles: str, dry_run: bool = False) -> tuple[BackupService, Path]:
    require, ident = make_require(*roles)
    guard, _, _, _ = make_guard(dry_run=dry_run, yes=True)
    service = BackupService(
        LocalBackupCatalog(tmp_path),
        ConfigFileProtector(),
        require,
        make_audit(tmp_path, ident),
        guard,
    )
    return service, tmp_path


def _changed_file(tmp_path: Path) -> Path:
    target = tmp_path / "settings.json"
    target.write_text('{"v": 1}', encoding="utf-8")
    ConfigFileProtector().write_text(target, '{"v": 2}')
    return target


def test_backup_list_and_restore_roundtrip(tmp_path: Path) -> None:
    target = _changed_file(tmp_path)
    service, _ = _service(tmp_path, "developer")
    entries = service.list_backups()
    assert len(entries) == 1 and entries[0].original == target
    assert service.list_backups(file="nomatch") == []
    service.restore(entries[0].id)
    assert target.read_text(encoding="utf-8") == '{"v": 1}'
    # restoring backed up the v2 content first, so the restore itself is reversible
    assert len(service.list_backups()) == 2


def test_backup_restore_dry_run_changes_nothing(tmp_path: Path) -> None:
    target = _changed_file(tmp_path)
    service, _ = _service(tmp_path, "developer", dry_run=True)
    entry = service.list_backups()[0]
    assert service.restore(entry.id) is None
    assert target.read_text(encoding="utf-8") == '{"v": 2}'


def test_backup_restore_requires_write_permission(tmp_path: Path) -> None:
    _changed_file(tmp_path)
    service, _ = _service(tmp_path, "viewer")
    entry = service.list_backups()[0]
    with pytest.raises(AuthorizationError):
        service.restore(entry.id)


def test_backup_unknown_id(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, "developer")
    with pytest.raises(ResourceNotFoundError):
        service.restore("missing")


def test_backup_commands(cli_sandbox) -> None:  # type: ignore[no-untyped-def]
    project = cli_sandbox("developer")
    assert "No backups" in runner.invoke(app, ["backup", "list"]).output
    target = _changed_file(project)
    listed = runner.invoke(app, ["backup", "list"])
    assert listed.exit_code == 0 and "settings.json" in listed.output
    entry = LocalBackupCatalog(project).list()[0]
    dry = runner.invoke(app, ["backup", "restore", entry.id, "--dry-run"])
    assert dry.exit_code == 0 and target.read_text(encoding="utf-8") == '{"v": 2}'
    done = runner.invoke(app, ["backup", "restore", entry.id, "--yes"])
    assert done.exit_code == 0 and target.read_text(encoding="utf-8") == '{"v": 1}'


# ---- documentation contract (M28) -----------------------------------------------------------------------------------
def test_openapi_contract_is_valid_and_disciplined() -> None:
    import yaml

    doc = yaml.safe_load((Path(__file__).parents[2] / "docs" / "openapi.yaml").read_text("utf-8"))
    assert str(doc["openapi"]).startswith("3.1")
    assert doc["info"]["title"] and doc["info"]["version"]
    for path, item in doc["paths"].items():
        assert path.startswith("/v1/"), path
        for method, operation in item.items():
            assert operation.get("responses"), f"{method} {path} declares no responses"
            assert "security" in operation or "security" in doc, f"{method} {path} has no security"


def test_cli_reference_lists_every_command() -> None:
    text = (Path(__file__).parents[2] / "docs" / "CLI_REFERENCE.md").read_text("utf-8")
    for name in LAZY_COMMANDS:
        assert f"stratos {name}" in text, f"{name} missing from CLI_REFERENCE.md"


def test_npm_package_version_matches_python_version() -> None:
    import json
    import tomllib

    root = Path(__file__).parents[2]
    python_version = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"][
        "version"
    ]
    npm = json.loads((root / "npm" / "package.json").read_text("utf-8"))
    assert npm["version"] == python_version
    assert npm["bin"]["stratos"] == "bin/stratos.js"
    assert (root / "npm" / "bin" / "stratos.js").is_file()
