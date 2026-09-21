"""Shared CLI fixtures: isolated config, in-memory keyring, and a helper to 'sign in'."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from stratos.infrastructure.secrets import InMemorySecretStore

ISSUER = "https://idp.example.com"


@pytest.fixture
def cli_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Path]:
    """Returns sign_in(*roles) -> project dir. Config, keyring, cache and audit are all isolated."""
    from stratos.config import loader
    from stratos.infrastructure import secrets as secrets_module
    from stratos.infrastructure.filesystem import audit_log, cache

    monkeypatch.setattr(loader, "user_config_path", lambda: tmp_path / "user.yaml")
    monkeypatch.setattr(loader, "org_config_path", lambda: tmp_path / "org.yaml")
    monkeypatch.setattr(loader, "project_config_path", lambda cwd=None: tmp_path / "project.yaml")
    monkeypatch.setattr(audit_log, "default_audit_path", lambda: tmp_path / "audit.jsonl")
    monkeypatch.setattr(cache, "user_cache_dir", lambda *a, **k: str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)
    store = InMemorySecretStore()
    monkeypatch.setattr(secrets_module, "KeyringSecretStore", lambda *a, **k: store)
    from typer.testing import CliRunner

    from stratos.cli.app import app

    runner = CliRunner()
    runner.invoke(app, ["config", "set", "auth.issuer", ISSUER])
    runner.invoke(app, ["config", "set", "auth.client_id", "cid"])

    def sign_in(*roles: str) -> Path:
        store.set(f"{ISSUER}|access", "tok-abcdefghijkl")
        identity = {"subject": "u1", "email": "dev@example.com", "organisation": "acme"}
        store.set(
            f"{ISSUER}|meta",
            json.dumps({"expires_at": None, "identity": {**identity, "roles": list(roles)}}),
        )
        return tmp_path

    return sign_in
