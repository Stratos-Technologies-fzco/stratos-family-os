"""Layered configuration loading.

Precedence, highest first: CLI arguments > environment variables >
project config > user config > organisation defaults > built-in defaults.
No global state: every function takes and returns values.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from platformdirs import site_config_dir, user_config_dir
from pydantic import ValidationError as PydanticValidationError

from stratos.config.settings import Settings
from stratos.domain.exceptions import ConfigurationError
from stratos.utils.redaction import is_sensitive_key

CONFIG_FILENAME = "config.yaml"


def user_config_path() -> Path:
    return Path(user_config_dir("stratos", appauthor=False)) / CONFIG_FILENAME


def org_config_path() -> Path:
    return Path(site_config_dir("stratos", appauthor=False)) / CONFIG_FILENAME


def project_config_path(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / ".stratos" / CONFIG_FILENAME


def deep_merge(base: Mapping[str, Any], top: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in top.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _find_secret_keys(data: Mapping[str, Any], prefix: str = "") -> list[str]:
    found: list[str] = []
    for key, value in data.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, Mapping):
            found += _find_secret_keys(value, f"{dotted}.")
        elif is_sensitive_key(str(key)):
            found.append(dotted)
    return found


def reject_secrets(data: Mapping[str, Any], source: str) -> None:
    keys = _find_secret_keys(data)
    if keys:
        raise ConfigurationError(
            f"Secrets must not be stored in configuration ({source}): {', '.join(keys)}",
            hint="Use the OS keyring (stratos login) or environment variables instead.",
        )


def read_yaml(path: Path) -> dict[str, Any]:
    """Read one YAML layer; a missing file is an empty layer."""
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError) as exc:
        raise ConfigurationError(f"Cannot read configuration file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"Configuration file {path} must contain a mapping.")
    reject_secrets(data, str(path))
    return data


def write_yaml(path: Path, data: Mapping[str, Any]) -> None:
    reject_secrets(data, str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(dict(data), sort_keys=True), encoding="utf-8")


def validate(data: Mapping[str, Any]) -> Settings:
    try:
        return Settings(**data)
    except PydanticValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise ConfigurationError(f"Invalid configuration: {details}") from exc


def load_settings(
    cli_overrides: Mapping[str, Any] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    org_path: Path | None = None,
    user_path: Path | None = None,
    project_path: Path | None = None,
) -> Settings:
    layers: list[Mapping[str, Any]] = [
        read_yaml(org_path or org_config_path()),
        read_yaml(user_path or user_config_path()),
        read_yaml(project_path or project_config_path()),
        _env_layer(env),
        {k: v for k, v in (cli_overrides or {}).items() if v is not None},
    ]
    merged: dict[str, Any] = {}
    for layer in layers:
        merged = deep_merge(merged, layer)
    return validate(merged)


def _env_layer(env: Mapping[str, str] | None) -> dict[str, Any]:
    """STRATOS_API__ENDPOINT=x  ->  {"api": {"endpoint": "x"}}"""
    prefix = Settings.model_config["env_prefix"]
    out: dict[str, Any] = {}
    for name, value in (os.environ if env is None else env).items():
        if not name.upper().startswith(prefix) or not value:
            continue
        *parents, leaf = name[len(prefix) :].lower().split("__")
        if (parents[0] if parents else leaf) not in Settings.model_fields:
            continue  # e.g. STRATOS_DEBUG is not a setting
        node = out
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return out


def set_value(path: Path, dotted_key: str, value: str) -> Settings:
    """Set one dotted key in a YAML layer after validating the result."""
    data = read_yaml(path)
    node = data
    *parents, leaf = dotted_key.split(".")
    for part in parents:
        child = node.setdefault(part, {})
        if not isinstance(child, dict):
            raise ConfigurationError(f"'{dotted_key}' is not a valid configuration key.")
        node = child
    node[leaf] = value
    reject_secrets(data, dotted_key)
    try:
        settings = validate(data)  # raises on unknown keys / bad values
    except ConfigurationError as first:
        node[leaf] = [v.strip() for v in value.split(",") if v.strip()]  # list-valued settings
        try:
            settings = validate(data)
        except ConfigurationError:
            raise first from None
    write_yaml(path, data)
    return settings


def reset_value(path: Path, dotted_key: str | None) -> None:
    """Remove one key (or the whole layer when dotted_key is None)."""
    if dotted_key is None:
        path.unlink(missing_ok=True)
        return
    data = read_yaml(path)
    node = data
    *parents, leaf = dotted_key.split(".")
    for part in parents:
        node = node.get(part, {})
        if not isinstance(node, dict):
            return
    node.pop(leaf, None)
    write_yaml(path, data)
