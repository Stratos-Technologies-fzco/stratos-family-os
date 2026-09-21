"""Local skill registry: a directory with index.json plus one folder per skill.

index.json: {"skills": [{"name", "version", "description", "source", "checksum",
                          "permissions": [...], "compatibility": {"stratos": ">=0.1.0"},
                          "path": "optional folder, defaults to name"}]}
"""

import json
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from stratos.domain.exceptions import ConfigurationError, ResourceNotFoundError
from stratos.domain.models.extensions import SkillManifest
from stratos.utils.checksum import compute_checksum

MAX_SKILL_BYTES = 5 * 1024 * 1024
__all__ = ["LocalSkillRegistry", "compute_checksum"]


class LocalSkillRegistry:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _index(self) -> list[dict[str, object]]:
        path = self._root / "index.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigurationError(f"Skill registry not found at {path}.") from exc
        except ValueError as exc:
            raise ConfigurationError(f"Skill registry index {path} is not valid JSON.") from exc
        skills = data.get("skills") if isinstance(data, dict) else None
        if not isinstance(skills, list):
            raise ConfigurationError(f"Skill registry index {path} needs a 'skills' list.")
        return skills

    def list(self) -> list[SkillManifest]:
        manifests = []
        for entry in self._index():
            fields = {k: v for k, v in entry.items() if k != "path"}
            try:
                manifests.append(SkillManifest.model_validate(fields))
            except PydanticValidationError as exc:
                raise ConfigurationError(
                    f"Invalid skill entry '{entry.get('name')}': {exc.error_count()} error(s)."
                ) from exc
        return manifests

    def get(self, name: str) -> SkillManifest | None:
        return next((m for m in self.list() if m.name == name), None)

    def files(self, name: str) -> dict[str, bytes]:
        entry = next((e for e in self._index() if e.get("name") == name), None)
        if entry is None:
            raise ResourceNotFoundError(f"Skill '{name}' is not in the registry.")
        folder = (self._root / str(entry.get("path") or name)).resolve()
        if not folder.is_dir() or self._root.resolve() not in folder.parents:
            raise ResourceNotFoundError(f"Skill folder for '{name}' was not found in the registry.")
        out: dict[str, bytes] = {}
        total = 0
        for file in sorted(folder.rglob("*")):
            if file.is_symlink() or not file.is_file():
                continue  # never follow links out of the skill folder
            data = file.read_bytes()
            total += len(data)
            if total > MAX_SKILL_BYTES:
                raise ConfigurationError(
                    f"Skill '{name}' exceeds the {MAX_SKILL_BYTES // 1024} KB limit."
                )
            out[file.relative_to(folder).as_posix()] = data
        return out
