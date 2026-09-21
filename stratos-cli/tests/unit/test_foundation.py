import json
import logging
from pathlib import Path

import pytest
import yaml
from rich.console import Console
from typer.testing import CliRunner

from stratos.cli import context as context_module
from stratos.cli.app import app
from stratos.cli.errors import exit_code_for, handle_error
from stratos.cli.output import ConsoleRenderer
from stratos.config.loader import load_settings, read_yaml
from stratos.domain import exceptions as exc
from stratos.domain.enums import ExitCode, OutputFormat
from stratos.logging import configure_logging
from stratos.utils.redaction import REDACTED, SecretRedactor

runner = CliRunner()


# ---- M04: exit codes -------------------------------------------------------
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (exc.StratosError("x"), 1),
        (exc.AuthenticationError("x"), 3),
        (exc.AuthorizationError("x"), 4),
        (exc.ResourceNotFoundError("x"), 5),
        (exc.ValidationError("x"), 6),
        (exc.NetworkError("x"), 7),
        (exc.APIError("x"), 7),
        (exc.ConfigurationError("x"), 8),
        (exc.DependencyError("x"), 9),
        (exc.OperationCancelledError("x"), 10),
        (RuntimeError("x"), 1),
    ],
)
def test_exit_codes(error: BaseException, code: int) -> None:
    assert exit_code_for(error) == code


def _renderer() -> tuple[ConsoleRenderer, Console, Console]:
    out = Console(record=True, width=120, force_terminal=False)
    err = Console(record=True, width=120, force_terminal=False, stderr=True)
    return ConsoleRenderer(OutputFormat.TABLE, console=out, err_console=err), out, err


def test_traceback_only_with_debug_and_redacted() -> None:
    r, _, err = _renderer()
    error = RuntimeError("boom token=abc123secret")
    handle_error(error, r, debug=False)
    text = err.export_text()
    assert "Traceback" not in text and "abc123secret" not in text
    r, _, err = _renderer()
    try:
        raise error
    except RuntimeError as caught:
        handle_error(caught, r, debug=True)
    assert "Traceback" in err.export_text() and "abc123secret" not in err.export_text()


# ---- M05: redaction & logging ---------------------------------------------
@pytest.mark.parametrize(
    "secret",
    [
        "ghp_" + "a" * 30,
        "sk-" + "b" * 30,
        "Bearer abcdefghij123456",
        "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.abcdefghijkl",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
        "password=hunter2hunter2",
        "refresh_token: xyz98765",
    ],
)
def test_redacts_secret_categories(secret: str) -> None:
    out = SecretRedactor().redact_text(f"before {secret} after")
    assert REDACTED in out
    assert "hunter2" not in out and "abcdefghij123456" not in out and "MIIabc" not in out


def test_redacts_nested_structures() -> None:
    data = {"name": "x", "auth": {"api_key": "k", "items": [{"password": "p"}]}}
    out = SecretRedactor().redact(data)
    assert out["auth"]["api_key"] == REDACTED
    assert out["auth"]["items"][0]["password"] == REDACTED
    assert out["name"] == "x"


def test_logs_are_redacted_and_carry_ids(capsys: pytest.CaptureFixture[str]) -> None:
    from stratos.domain.enums import LogLevel

    log = configure_logging(LogLevel.INFO)
    log.info("using token=supersecretvalue")
    err = capsys.readouterr().err
    assert "supersecretvalue" not in err and "req=" in err and "corr=" in err


def test_log_levels() -> None:
    from stratos.domain.enums import LogLevel
    from stratos.logging import level_for

    assert level_for(False, False) is LogLevel.WARNING
    assert level_for(True, False) is LogLevel.INFO
    assert level_for(True, True) is LogLevel.DEBUG
    assert configure_logging(LogLevel.DEBUG).level == logging.DEBUG


# ---- M02: output -----------------------------------------------------------
ROWS = [{"name": "alpha", "token": "s3cret"}, {"name": "beta", "token": "t0ken"}]


def _capture(fmt: OutputFormat) -> str:
    out = Console(record=True, width=120, force_terminal=False)
    ConsoleRenderer(fmt, console=out).data(ROWS)
    return out.export_text()


def test_json_and_yaml_valid_and_redacted() -> None:
    parsed = json.loads(_capture(OutputFormat.JSON))
    assert parsed[0]["token"] == REDACTED
    assert yaml.safe_load(_capture(OutputFormat.YAML))[1]["name"] == "beta"


def test_table_plain_quiet() -> None:
    assert "alpha" in _capture(OutputFormat.TABLE) and "s3cret" not in _capture(OutputFormat.TABLE)
    assert _capture(OutputFormat.PLAIN).splitlines()[0].startswith("alpha")
    assert _capture(OutputFormat.QUIET) == ""


def test_quiet_suppresses_messages() -> None:
    out = Console(record=True, force_terminal=False)
    ConsoleRenderer(OutputFormat.QUIET, console=out).success("done")
    assert out.export_text() == ""


# ---- M03: configuration ----------------------------------------------------
def test_defaults(tmp_path: Path) -> None:
    s = load_settings(
        env={}, org_path=tmp_path / "o", user_path=tmp_path / "u", project_path=tmp_path / "p"
    )
    assert s.github.organization == "Stratos-Technologies-fzco"


def test_precedence_matrix(tmp_path: Path) -> None:
    def write(name: str, org: str) -> Path:
        p = tmp_path / name
        p.write_text(yaml.safe_dump({"github": {"organization": org}}))
        return p

    paths = {
        "org_path": write("org", "from-org"),
        "user_path": write("user", "from-user"),
        "project_path": write("project", "from-project"),
    }

    def get(env: dict[str, str], cli: dict[str, object] | None) -> str:
        return load_settings(cli, env=env, **paths).github.organization

    from_env = {"STRATOS_GITHUB__ORGANIZATION": "from-env"}
    assert get({}, None) == "from-project"
    assert get(from_env, None) == "from-env"
    assert get(from_env, {"github": {"organization": "cli"}}) == "cli"
    (tmp_path / "project").unlink()
    assert get({}, None) == "from-user"
    (tmp_path / "user").unlink()
    assert get({}, None) == "from-org"


def test_invalid_config_is_configuration_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("api: {endpoint: not-a-url}\n")
    with pytest.raises(exc.ConfigurationError):
        load_settings(env={}, org_path=tmp_path / "x", user_path=bad, project_path=tmp_path / "y")


def test_secrets_rejected_in_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "s.yaml"
    bad.write_text("api: {token: abc}\n")
    with pytest.raises(exc.ConfigurationError, match="Secrets"):
        read_yaml(bad)


# ---- M01: shell + config commands -----------------------------------------
@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from stratos.config import loader

    monkeypatch.setattr(loader, "user_config_path", lambda: tmp_path / "user.yaml")
    monkeypatch.setattr(loader, "org_config_path", lambda: tmp_path / "org.yaml")
    monkeypatch.setattr(loader, "project_config_path", lambda cwd=None: tmp_path / "project.yaml")
    return tmp_path


def test_help_and_version() -> None:
    assert runner.invoke(app, ["--help"]).exit_code == 0
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0 and r.output.startswith("stratos ")


def test_help_loads_no_settings_or_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("settings loaded during --help")

    monkeypatch.setattr(context_module, "load_settings", boom)
    assert runner.invoke(app, ["--help"]).exit_code == 0
    assert runner.invoke(app, ["config", "--help"]).exit_code == 0


def test_usage_error_exit_code_2() -> None:
    assert runner.invoke(app, ["--output", "nope", "config", "list"]).exit_code == 2


def test_config_set_get_list_reset(isolated: Path) -> None:
    r = runner.invoke(app, ["config", "set", "github.organization", "acme"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["-o", "json", "config", "get", "github.organization"])
    assert json.loads(r.output) == {"github.organization": "acme"}
    assert "api.endpoint" in runner.invoke(app, ["config", "list"]).output
    assert runner.invoke(app, ["config", "reset", "github.organization"]).exit_code == 0
    r = runner.invoke(app, ["-o", "json", "config", "get", "github.organization"])
    assert json.loads(r.output)["github.organization"] == "Stratos-Technologies-fzco"


def test_config_errors_map_to_exit_8(isolated: Path) -> None:
    assert runner.invoke(app, ["config", "get", "nope.key"]).exit_code == ExitCode.CONFIGURATION
    assert runner.invoke(app, ["config", "set", "api.endpoint", "bad"]).exit_code == 8
    assert runner.invoke(app, ["config", "set", "api.token", "x"]).exit_code == 8


def test_messages_do_not_crash_on_legacy_windows_code_pages() -> None:
    """cp1252 cannot encode the check/cross symbols; the renderer must fall back to ASCII."""
    import io

    def legacy() -> tuple[Console, io.BytesIO]:
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
        return Console(file=stream, force_terminal=False, width=100), raw

    (out, out_raw), (err, err_raw) = legacy(), legacy()
    r = ConsoleRenderer(OutputFormat.TABLE, console=out, err_console=err)
    r.success("done")
    r.info("note")
    r.warning("careful")
    r.error("bad", hint="fix it")
    out.file.flush()
    err.file.flush()
    shown = out_raw.getvalue().decode("cp1252") + err_raw.getvalue().decode("cp1252")
    assert "OK done" in shown and "i note" in shown and "x bad" in shown and "Hint: fix it" in shown
    utf, utf_raw = Console(record=True, width=80, force_terminal=False), None
    ConsoleRenderer(OutputFormat.TABLE, console=utf).success("done")
    assert "\u2714 done" in utf.export_text() and utf_raw is None  # real terminals keep the symbols
