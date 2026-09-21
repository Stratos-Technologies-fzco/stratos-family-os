"""Finds the backups written by `ConfigFileProtector` and `ClaudeCodeManager.remove_skill`."""

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from stratos.domain.exceptions import ValidationError
from stratos.domain.models.files import BackupEntry

_FILE = re.compile(r"^(?P<original>.+)\.(?P<stamp>\d{8}T\d{12}Z)\.bak$")
_SKILL = re.compile(r"^(?P<name>.+)\.(?P<stamp>\d{8}T\d{12}Z)$")
_SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__"}


def _iso(stamp: str) -> str:
    return datetime.strptime(stamp, "%Y%m%dT%H%M%S%fZ").isoformat()


class LocalBackupCatalog:
    def __init__(self, project_dir: Path) -> None:
        self._root = project_dir

    def _backup_dirs(self) -> list[Path]:
        found: list[Path] = []
        for current, dirs, _ in os.walk(self._root):
            dirs[:] = [d for d in dirs if d not in _SKIP]
            here = Path(current)
            if here.name == "backups" and here.parent.name == ".stratos":
                found.append(here)
                dirs[:] = []
        return found

    def list(self) -> list[BackupEntry]:
        entries: list[BackupEntry] = []
        for folder in self._backup_dirs():
            base = folder.parent.parent  # the directory the backed-up files live in
            for item in folder.iterdir():
                match = _FILE.match(item.name)
                if item.is_file() and match:
                    entries.append(
                        BackupEntry(
                            id=item.relative_to(self._root).as_posix(),
                            kind="file",
                            original=base / match["original"],
                            backup=item,
                            created=_iso(match["stamp"]),
                            size=item.stat().st_size,
                        )
                    )
            skills = folder / "skills"
            for item in skills.iterdir() if skills.is_dir() else []:
                match = _SKILL.match(item.name)
                if item.is_dir() and match:
                    entries.append(
                        BackupEntry(
                            id=item.relative_to(self._root).as_posix(),
                            kind="skill",
                            original=base / ".claude" / "skills" / match["name"],
                            backup=item,
                            created=_iso(match["stamp"]),
                            size=sum(f.stat().st_size for f in item.rglob("*") if f.is_file()),
                        )
                    )
        return sorted(entries, key=lambda e: e.created, reverse=True)

    def read(self, entry: BackupEntry) -> bytes:
        return entry.backup.read_bytes()

    def restore_skill(self, entry: BackupEntry) -> None:
        if entry.original.exists():
            raise ValidationError(
                f"Skill folder {entry.original.name} already exists; it was not overwritten.",
                hint="Remove the skill first (`stratos skill remove`), then restore.",
            )
        entry.original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(entry.backup, entry.original)
