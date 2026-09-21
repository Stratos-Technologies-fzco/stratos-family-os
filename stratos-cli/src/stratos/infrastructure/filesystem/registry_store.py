"""Local registries for projects and workspaces (adapters until the Platform API stores them).

Both write atomically and refuse to overwrite a corrupt file (a damaged registry is reported,
never silently replaced).
"""

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir
from pydantic import ValidationError as PydanticValidationError

from stratos.domain.exceptions import ConfigurationError
from stratos.domain.models.workflow import ProjectRecord, WorkspaceRecord


def _load(path: Path, key: str) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("not an object")
        items = data.get(key, {})
        if not isinstance(items, dict):
            raise ValueError("not a mapping")
        return items
    except (OSError, ValueError) as exc:
        raise ConfigurationError(
            f"The registry file {path.name} is damaged: {type(exc).__name__}.",
            hint=f"Fix or remove {path} (a copy is safest).",
        ) from exc


def _save(path: Path, key: str, items: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".stratos-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({key: items}, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def default_registry_dir() -> Path:
    return Path(user_data_dir("stratos", appauthor=False))


class LocalProjectStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_registry_dir() / "projects.json"

    def _records(self) -> dict[str, ProjectRecord]:
        try:
            return {
                k: ProjectRecord.model_validate(v) for k, v in _load(self._path, "projects").items()
            }
        except PydanticValidationError as exc:
            raise ConfigurationError(f"The project registry {self._path.name} is damaged.") from exc

    def get(self, org: str, name: str) -> ProjectRecord | None:
        return self._records().get(f"{org}/{name}")

    def list(self, org: str | None = None) -> list[ProjectRecord]:
        found = [r for r in self._records().values() if org is None or r.org == org]
        return sorted(found, key=lambda r: r.key.lower())

    def save(self, record: ProjectRecord) -> None:
        items = _load(self._path, "projects")
        items[record.key] = record.model_dump(mode="json")
        _save(self._path, "projects", items)

    def delete(self, org: str, name: str) -> bool:
        items = _load(self._path, "projects")
        if items.pop(f"{org}/{name}", None) is None:
            return False
        _save(self._path, "projects", items)
        return True


class LocalWorkspaceStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_registry_dir() / "workspaces.json"

    def _records(self) -> dict[str, WorkspaceRecord]:
        try:
            return {
                k: WorkspaceRecord.model_validate(v)
                for k, v in _load(self._path, "workspaces").items()
            }
        except PydanticValidationError as exc:
            raise ConfigurationError(
                f"The workspace registry {self._path.name} is damaged."
            ) from exc

    def get(self, name: str) -> WorkspaceRecord | None:
        return self._records().get(name)

    def list(self) -> list[WorkspaceRecord]:
        return sorted(self._records().values(), key=lambda r: r.name.lower())

    def save(self, record: WorkspaceRecord) -> None:
        items = _load(self._path, "workspaces")
        items[record.name] = record.model_dump(mode="json")
        _save(self._path, "workspaces", items)

    def delete(self, name: str) -> bool:
        items = _load(self._path, "workspaces")
        if items.pop(name, None) is None:
            return False
        _save(self._path, "workspaces", items)
        return True
