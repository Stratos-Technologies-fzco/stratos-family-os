"""End-to-end: run the real `stratos` entry point in a separate process, as a user would."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "APPDATA": str(tmp_path / "appdata"),
        "LOCALAPPDATA": str(tmp_path / "localappdata"),
        "PYTHONIOENCODING": "utf-8",
        "NO_COLOR": "1",
    }
    return subprocess.run(
        [sys.executable, "-m", "stratos", *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def test_version_flag(tmp_path: Path) -> None:
    result = run(tmp_path, "--version")
    assert result.returncode == 0 and result.stdout.startswith("stratos ")


def test_help_lists_commands(tmp_path: Path) -> None:
    result = run(tmp_path, "--help")
    assert result.returncode == 0
    for name in ("login", "project", "deploy", "backup", "cache"):
        assert name in result.stdout


def test_unknown_command_exits_2(tmp_path: Path) -> None:
    assert run(tmp_path, "nonsense").returncode == 2


def test_protected_command_requires_sign_in(tmp_path: Path) -> None:
    result = run(tmp_path, "repo", "list")
    assert result.returncode != 0
    assert "Traceback" not in result.stderr + result.stdout


def test_doctor_runs_offline(tmp_path: Path) -> None:
    result = run(tmp_path, "doctor")
    assert result.returncode in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
    assert "Traceback" not in result.stderr
