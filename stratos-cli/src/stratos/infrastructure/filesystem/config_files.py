"""Configuration-file protection (M24): backup, diff, merge, validation and rollback.

Developer-owned files are never overwritten blindly: existing JSON is merged, Markdown is only
touched inside a Stratos-managed block, every change is backed up first and can be rolled back.
"""

import difflib
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stratos.config.loader import deep_merge
from stratos.domain.exceptions import ConfigurationError, ValidationError
from stratos.utils.managed_block import BEGIN, END, set_managed_block  # noqa: F401
from stratos.utils.redaction import SecretRedactor


@dataclass(frozen=True)
class WriteResult:
    path: Path
    changed: bool
    created: bool
    backup: Path | None
    diff: str


class ConfigFileProtector:
    def __init__(self, redactor: SecretRedactor | None = None, *, dry_run: bool = False) -> None:
        self._redactor = redactor or SecretRedactor()
        self.dry_run = dry_run  # compute results and diffs, but write nothing

    # ---- building blocks -----------------------------------------------------------------
    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".stratos-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def backup(self, path: Path) -> Path | None:
        if not path.is_file():
            return None
        folder = path.parent / ".stratos" / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        target = folder / f"{path.name}.{stamp}.bak"
        shutil.copy2(path, target)
        return target

    @staticmethod
    def diff(path: Path, new_text: str) -> str:
        old = path.read_text(encoding="utf-8") if path.is_file() else ""
        return "".join(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"{path.name} (current)",
                tofile=f"{path.name} (proposed)",
            )
        )

    def _commit(self, path: Path, new_text: str) -> WriteResult:
        old_exists = path.is_file()
        if old_exists and path.read_text(encoding="utf-8") == new_text:
            return WriteResult(path, False, False, None, "")
        diff = self.diff(path, new_text)
        if self.dry_run:
            return WriteResult(path, True, not old_exists, None, diff)
        backup = self.backup(path)
        self._atomic_write(path, new_text)
        return WriteResult(path, True, not old_exists, backup, diff)

    def write_text(self, path: Path, text: str) -> WriteResult:
        """Write a whole text file (backed up first; honours dry-run)."""
        return self._commit(path, text)

    def rollback(self, result: WriteResult) -> None:
        """Undo one write: restore the backup, or remove a file that did not exist before."""
        if not result.changed:
            return
        if result.backup and result.backup.is_file():
            shutil.copy2(result.backup, result.path)
        elif result.created:
            result.path.unlink(missing_ok=True)

    def write_bytes(self, path: Path, data: bytes) -> WriteResult:
        """Write a (possibly binary) file atomically, backing up any different existing file."""
        exists = path.is_file()
        if exists and path.read_bytes() == data:
            return WriteResult(path, False, False, None, "")
        if self.dry_run:
            return WriteResult(path, True, not exists, None, "")
        backup = self.backup(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".stratos-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return WriteResult(path, True, not exists, backup, "")

    # ---- JSON ----------------------------------------------------------------------------
    def read_json(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError as exc:
            raise ConfigurationError(
                f"{path.name} is not valid JSON; refusing to modify it.",
                hint="Fix or remove the file, then retry.",
            ) from exc
        if not isinstance(data, dict):
            raise ConfigurationError(f"{path.name} must contain a JSON object.")
        return data

    def merge_json(
        self,
        path: Path,
        patch: Mapping[str, Any],
        *,
        validate: Callable[[dict[str, Any]], None] | None = None,
        forbid_secrets: bool = True,
    ) -> WriteResult:
        merged = deep_merge(self.read_json(path), patch)
        if validate:
            validate(merged)
        text = json.dumps(merged, indent=2, sort_keys=True) + "\n"
        if forbid_secrets and self._redactor.redact_text(text) != text:
            raise ValidationError(
                f"Refusing to write {path.name}: it would contain a secret.",
                hint="Reference secrets through environment variables, e.g. ${API_KEY}.",
            )
        return self._commit(path, text)

    def remove_json_key(self, path: Path, *keys: str) -> WriteResult:
        data = self.read_json(path)
        node: Any = data
        for key in keys[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(keys[-1], None)
        return self._commit(path, json.dumps(data, indent=2, sort_keys=True) + "\n")

    # ---- Markdown managed block ----------------------------------------------------------
    def write_managed_block(self, path: Path, block_id: str, content: str) -> WriteResult:
        """Set the Stratos-managed section of a Markdown file; everything else is untouched."""
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        return self._commit(path, set_managed_block(existing, block_id, content))
