"""Skills: discover, install, update and remove without trusting them blindly.

A skill is verified (compatibility, checksum, declared permissions) before anything is written.
Skills are only ever copied into the project; Stratos never executes them.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from stratos.application.audit_service import AuditService
from stratos.application.safety import OperationGuard
from stratos.domain.enums import AuditAction, AuditResult, Permission
from stratos.domain.exceptions import ResourceNotFoundError, StratosError, ValidationError
from stratos.domain.interfaces import SkillRegistry
from stratos.domain.models.auth import Identity
from stratos.domain.models.extensions import SkillManifest
from stratos.infrastructure.claude.manager import ClaudeCodeManager
from stratos.infrastructure.filesystem.cache import TtlCache
from stratos.infrastructure.filesystem.config_files import ConfigFileProtector
from stratos.infrastructure.filesystem.skill_registry import compute_checksum

Require = Callable[[Permission], Identity]
CACHE_TTL = 300.0


@dataclass(frozen=True)
class SkillRow:
    manifest: SkillManifest
    installed_version: str | None


@dataclass(frozen=True)
class InstallOutcome:
    name: str
    version: str
    action: Literal["installed", "updated", "unchanged"]


def check_compatibility(manifest: SkillManifest, stratos_version: str) -> None:
    spec = manifest.compatibility.get("stratos")
    if not spec:
        return
    try:
        ok = SpecifierSet(spec).contains(Version(stratos_version), prereleases=True)
    except (InvalidSpecifier, InvalidVersion) as exc:
        raise ValidationError(
            f"Skill '{manifest.name}' has an invalid compatibility rule."
        ) from exc
    if not ok:
        raise ValidationError(
            f"Skill '{manifest.name}' requires stratos {spec} (this is {stratos_version})."
        )


class SkillService:
    def __init__(
        self,
        registry: SkillRegistry,
        claude: ClaudeCodeManager,
        protector: ConfigFileProtector,
        require: Require,
        audit: AuditService,
        guard: OperationGuard,
        cache: TtlCache,
        *,
        stratos_version: str,
        registry_id: str,
    ) -> None:
        self._registry = registry
        self._claude = claude
        self._protector = protector
        self._require = require
        self._audit = audit
        self._guard = guard
        self._cache = cache
        self._version = stratos_version
        self._registry_id = registry_id

    @property
    def _lock_path(self) -> Path:
        return self._claude.project_dir / ".stratos" / "skills.lock.json"

    def installed(self) -> dict[str, dict[str, Any]]:
        skills = self._protector.read_json(self._lock_path).get("skills", {})
        return skills if isinstance(skills, dict) else {}

    # ---- list ----------------------------------------------------------------------------
    def list_skills(self) -> list[SkillRow]:
        self._require(Permission.ORG_READ)
        data = self._cache.get_or_load(
            "skills",
            self._registry_id,
            CACHE_TTL,
            lambda: [m.model_dump(mode="json") for m in self._registry.list()],
        )
        installed = self.installed()
        return [
            SkillRow(m, installed.get(m.name, {}).get("version"))
            for m in (SkillManifest.model_validate(d) for d in data)
        ]

    # ---- install / update ----------------------------------------------------------------
    def _check_compatibility(self, manifest: SkillManifest) -> None:
        check_compatibility(manifest, self._version)

    def install(self, name: str) -> InstallOutcome | None:
        """Install one skill. Returns None in dry-run mode."""
        self._require(Permission.SKILL_INSTALL)
        return self._install(name)

    def _verify(self, name: str) -> tuple[SkillManifest, dict[str, bytes]]:
        manifest = self._registry.get(name)
        if manifest is None:
            raise ResourceNotFoundError(f"Skill '{name}' is not in the registry.")
        self._check_compatibility(manifest)
        files = self._registry.files(name)
        if compute_checksum(files) != manifest.checksum:
            raise ValidationError(
                f"Checksum mismatch for skill '{name}'; it was not installed.",
                hint="The skill contents differ from what the registry declared.",
            )
        return manifest, files

    def _install(self, name: str) -> InstallOutcome | None:
        try:
            manifest, files = self._verify(name)
        except StratosError:
            self._audit.emit(AuditAction.SKILL_INSTALL, "skill", name, result=AuditResult.FAILURE)
            raise
        lock = self.installed().get(name)
        if (
            lock
            and lock.get("checksum") == manifest.checksum
            and lock.get("version") == manifest.version
        ):
            return InstallOutcome(name, manifest.version, "unchanged")
        steps = [f"Install skill '{name}' {manifest.version} ({len(files)} files)"]
        if manifest.permissions:
            steps.append(f"Declared permissions: {', '.join(manifest.permissions)}")
        if self._guard.preview("skill install", steps):
            return None
        permissions_changed = not lock or set(lock.get("permissions", [])) != set(
            manifest.permissions
        )
        if manifest.permissions and permissions_changed:
            self._guard.confirm(
                f"Skill '{name}' declares these permissions:", list(manifest.permissions)
            )
        details = {"version": manifest.version, "checksum": manifest.checksum}
        with self._audit.record(AuditAction.SKILL_INSTALL, "skill", name, details):
            results = self._claude.install_skill(name, files)
            try:
                self._protector.merge_json(
                    self._lock_path,
                    {
                        "skills": {
                            name: {
                                "version": manifest.version,
                                "checksum": manifest.checksum,
                                "permissions": list(manifest.permissions),
                            }
                        }
                    },
                    forbid_secrets=False,
                )
            except BaseException:
                for r in reversed(results):
                    self._protector.rollback(r)
                raise
        return InstallOutcome(name, manifest.version, "updated" if lock else "installed")

    def update(self, name: str | None = None) -> list[InstallOutcome]:
        self._require(Permission.SKILL_INSTALL)
        installed = self.installed()
        names = [name] if name else sorted(installed)
        if name and name not in installed:
            raise ResourceNotFoundError(f"Skill '{name}' is not installed.")
        outcomes: list[InstallOutcome] = []
        for n in names:
            manifest = self._registry.get(n)
            if manifest is None or not self._is_newer(
                manifest.version, installed[n].get("version")
            ):
                if manifest is not None:
                    outcomes.append(InstallOutcome(n, manifest.version, "unchanged"))
                continue
            outcome = self._install(n)
            if outcome:
                outcomes.append(outcome)
        return outcomes

    @staticmethod
    def _is_newer(candidate: str, current: str | None) -> bool:
        if current is None:
            return True
        try:
            return Version(candidate) > Version(current)
        except InvalidVersion:
            return candidate != current

    # ---- remove --------------------------------------------------------------------------
    def remove(self, name: str) -> Path | None:
        self._require(Permission.SKILL_REMOVE)
        if name not in self.installed() and name not in self._claude.installed_skill_names():
            raise ResourceNotFoundError(f"Skill '{name}' is not installed.")
        if self._guard.preview("skill remove", [f"Remove skill '{name}' (a backup is kept)"]):
            return None
        self._guard.confirm_destructive(
            "Removing", [f"Skill '{name}' is deleted from .claude/skills"]
        )
        with self._audit.record(AuditAction.SKILL_REMOVE, "skill", name):
            backup = self._claude.remove_skill(name)
            self._protector.remove_json_key(self._lock_path, "skills", name)
        return backup
