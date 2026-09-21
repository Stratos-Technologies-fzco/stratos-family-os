"""`stratos init` (M20): bring a folder into the standard Stratos setup, safely and repeatably.

Only missing pieces are added. Developer-owned files are merged or edited inside Stratos blocks,
backed up first, shown as a diff, and rolled back together if a later step fails. Running it twice
gives the same result (the second run reports everything as unchanged).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from stratos.application.audit_service import AuditService
from stratos.application.mcp_service import McpService
from stratos.application.safety import OperationGuard
from stratos.application.skill_service import SkillService
from stratos.domain.enums import AuditAction, Permission
from stratos.domain.exceptions import ConfigurationError, ValidationError
from stratos.domain.models.auth import Identity
from stratos.domain.models.workflow import ProjectManifest
from stratos.infrastructure.claude.manager import ClaudeCodeManager
from stratos.infrastructure.filesystem.config_files import ConfigFileProtector, WriteResult
from stratos.infrastructure.filesystem.templates import (
    CLAUDE_DENY_RULES,
    DEFAULT_INSTRUCTIONS,
    GITIGNORE_LINES,
    TEMPLATES,
)
from stratos.utils.redaction import SecretRedactor
from stratos.utils.validation import validate_name

Require = Callable[[Permission], Identity]
MANIFEST = Path(".stratos") / "project.yaml"


def read_manifest(project_dir: Path) -> ProjectManifest | None:
    """The folder's Stratos manifest, or None. Raises ConfigurationError if it is unusable."""
    path = project_dir / MANIFEST
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return ProjectManifest.model_validate(data)
    except (yaml.YAMLError, PydanticValidationError, OSError) as exc:
        raise ConfigurationError(
            f"{MANIFEST.as_posix()} is not a valid Stratos manifest; it was left untouched.",
            hint="Fix it by hand or remove it and run `stratos init` again.",
        ) from exc


def detect_template(project_dir: Path) -> str:
    if (project_dir / "pyproject.toml").is_file() or (project_dir / "requirements.txt").is_file():
        return "python"
    if (project_dir / "package.json").is_file():
        return "node"
    return "none"


@dataclass(frozen=True)
class InitItem:
    name: str
    status: str  # created | updated | unchanged | present | skipped | would-change
    detail: str = ""
    diff: str = ""


class InitService:
    def __init__(
        self,
        project_dir: Path,
        require: Require,
        audit: AuditService,
        guard: OperationGuard,
        *,
        org: str,
        ai_provider: str,
        ai_model: str,
        instructions: str = DEFAULT_INSTRUCTIONS,
        knowledge_sources: Callable[[], list[str]] = list,
        skills: Callable[[], SkillService] | None = None,
        mcp: Callable[[], McpService] | None = None,
        redactor: SecretRedactor | None = None,
    ) -> None:
        self._dir = project_dir
        self._require = require
        self._audit = audit
        self._guard = guard
        self._org = org
        self._ai = (ai_provider, ai_model)
        self._instructions = instructions
        self._knowledge_sources = knowledge_sources
        self._skills = skills
        self._mcp = mcp
        self._redactor = redactor or SecretRedactor()

    # ---- detection -----------------------------------------------------------------------
    def detect(self) -> dict[str, str]:
        """What is already in place (read-only)."""
        d = self._dir
        claude = ClaudeCodeManager(d, ConfigFileProtector(self._redactor))
        try:
            manifest = read_manifest(d)
            project = f"yes ({manifest.name})" if manifest else "no"
        except ConfigurationError:
            project = "invalid manifest"
        pyproject = d / "pyproject.toml"
        standards = [
            n
            for n in (".editorconfig", "CONTRIBUTING.md", "ruff.toml", ".eslintrc.json")
            if (d / n).exists()
        ]
        if pyproject.is_file() and "[tool.ruff" in pyproject.read_text(
            encoding="utf-8", errors="replace"
        ):
            standards.append("pyproject.toml [tool.ruff]")
        return {
            "Git repository": "yes" if (d / ".git").exists() else "no",
            "Stratos project": project,
            "Claude Code": "detected" if claude.detect().detected else "not detected",
            "Skills installed": ", ".join(claude.installed_skill_names()) or "none",
            "MCP servers": ", ".join(sorted(claude.mcp_servers())) or "none",
            "Instructions (CLAUDE.md)": "yes" if (d / "CLAUDE.md").is_file() else "no",
            "Coding standards": ", ".join(standards) or "none found",
            "Environment file": ".env"
            if (d / ".env").is_file()
            else (".env.example" if (d / ".env.example").is_file() else "none"),
            "Stratos configuration": "yes" if (d / ".stratos" / "config.yaml").is_file() else "no",
        }

    # ---- init ----------------------------------------------------------------------------
    def init(
        self,
        *,
        name: str | None = None,
        template: str | None = None,
        skills: tuple[str, ...] = (),
        mcp: tuple[str, ...] = (),
    ) -> list[InitItem]:
        self._require(Permission.ORG_READ)
        if template is not None and template not in TEMPLATES:
            raise ValidationError(
                f"Unknown template '{template}'.", hint=f"Use one of: {', '.join(TEMPLATES)}."
            )
        dry = self._guard.options.dry_run
        protector = ConfigFileProtector(self._redactor, dry_run=dry)
        claude = ClaudeCodeManager(self._dir, protector)
        results: list[WriteResult] = []
        items: list[InitItem] = []

        def record(label: str, result: WriteResult, note: str = "") -> None:
            results.append(result)
            if dry:
                status = "would-change" if result.changed else "unchanged"
            else:
                status = (
                    ("created" if result.created else "updated") if result.changed else "unchanged"
                )
            items.append(InitItem(label, status, note, result.diff if result.changed else ""))

        def run_steps() -> None:
            items.append(self._manifest(protector, results, name, template, skills, mcp, dry))
            record(
                "CLAUDE.md instructions", claude.apply_instructions(organisation=self._instructions)
            )
            record("CLAUDE.md knowledge", claude.apply_knowledge(self._knowledge_sources()))
            record(".gitignore", self._gitignore(protector))
            record(
                ".claude/settings.json (protect secrets)", self._claude_settings(protector, claude)
            )
            if mcp:
                if dry:
                    items.append(InitItem("MCP servers", "would-change", ", ".join(mcp)))
                else:
                    assert self._mcp is not None, "MCP registry is not configured"
                    service = self._mcp()
                    for server in mcp:
                        outcome = service.install(server)
                        if outcome is not None:
                            record(f"MCP server {server}", outcome)
            if skills:
                if dry:
                    items.append(InitItem("Skills", "would-change", ", ".join(skills)))
                else:
                    assert self._skills is not None, "skills registry is not configured"
                    service_s = self._skills()
                    for skill in skills:
                        result = service_s.install(skill)
                        if result is not None:
                            items.append(InitItem(f"Skill {skill}", result.action, result.version))

        if dry:
            run_steps()
            self._guard.preview("init", [f"{i.name}: {i.status}" for i in items])
            return items
        try:
            with self._audit.record(AuditAction.PROJECT_INIT, "project", str(self._dir.name)):
                run_steps()
        except BaseException:
            for result in reversed(results):  # leave the folder as it was
                protector.rollback(result)
            raise
        return items

    # ---- pieces --------------------------------------------------------------------------
    def _manifest(
        self,
        protector: ConfigFileProtector,
        results: list[WriteResult],
        name: str | None,
        template: str | None,
        skills: tuple[str, ...],
        mcp: tuple[str, ...],
        dry: bool,
    ) -> InitItem:
        path = self._dir / MANIFEST
        if path.is_file():
            manifest = read_manifest(self._dir)  # validates; never rewritten
            return InitItem(
                "Project manifest", "present", f"kept ({manifest.name if manifest else ''})"
            )
        project_name = (
            re.sub(r"[^A-Za-z0-9._-]", "-", name or self._dir.name).strip("-.") or "project"
        )
        validate_name(project_name, "project name")
        data: dict[str, Any] = {
            "name": project_name,
            "organisation": self._org,
            "template": template or detect_template(self._dir),
            "skills": list(skills),
            "mcp_servers": list(mcp),
            "ai": {"provider": self._ai[0], "model": self._ai[1]},
        }
        result = protector.write_text(path, yaml.safe_dump(data, sort_keys=False))
        results.append(result)
        return InitItem(
            "Project manifest", "would-change" if dry else "created", project_name, result.diff
        )

    def _gitignore(self, protector: ConfigFileProtector) -> WriteResult:
        path = self._dir / ".gitignore"
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        have = {line.strip() for line in existing.splitlines()}
        missing = [line for line in GITIGNORE_LINES if line not in have]
        if not missing:
            return protector.write_text(path, existing)  # unchanged
        base = existing if not existing or existing.endswith("\n") else existing + "\n"
        block = ("\n" if base.strip() else "") + "# Stratos\n" + "\n".join(missing) + "\n"
        return protector.write_text(path, base + block)

    def _claude_settings(
        self, protector: ConfigFileProtector, claude: ClaudeCodeManager
    ) -> WriteResult:
        """Deny Claude Code access to .env files, keeping any rules the developer already set."""
        current = protector.read_json(claude.settings_path)
        raw = current.get("permissions")
        permissions: dict[str, Any] = raw if isinstance(raw, dict) else {}
        deny = list(permissions.get("deny", []))
        deny += [rule for rule in CLAUDE_DENY_RULES if rule not in deny]
        return claude.merge_settings({"permissions": {"deny": deny}})
