"""Project lifecycle (M19): create, list, get, update and delete projects.

Creation is one auditable operation made of named steps. The registry record stores which steps
finished, so a failure part-way leaves a `partial` project that the same command resumes; every
step is also individually idempotent (existence checks), so re-running never duplicates anything.
Nothing is deleted automatically on failure.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import yaml

from stratos.application.audit_service import AuditService
from stratos.application.mcp_service import to_config, validate_server
from stratos.application.policy_service import PolicyService
from stratos.application.safety import OperationGuard
from stratos.application.skill_service import check_compatibility
from stratos.config.loader import deep_merge
from stratos.domain.enums import AuditAction, AuditResult, Permission, ProjectStatus
from stratos.domain.exceptions import (
    AuthorizationError,
    ConfigurationError,
    ResourceNotFoundError,
    StratosError,
    ValidationError,
)
from stratos.domain.interfaces import GitHubPort, McpRegistry, ProjectStore, SkillRegistry
from stratos.domain.models.auth import Identity
from stratos.domain.models.workflow import (
    ProjectCreateResult,
    ProjectRecord,
    ProjectSpec,
    StepOutcome,
)
from stratos.domain.standards import (
    CI_PATH,
    CI_TEMPLATES,
    DEFAULT_INSTRUCTIONS,
    DEFAULT_LABELS,
    TEMPLATES,
    knowledge_block,
)
from stratos.utils.checksum import compute_checksum
from stratos.utils.managed_block import set_managed_block
from stratos.utils.paths import safe_relative
from stratos.utils.redaction import SecretRedactor
from stratos.utils.validation import validate_name, validate_slug

Require = Callable[[Permission], Identity]
_OWNER = re.compile(
    r"^@[A-Za-z0-9][A-Za-z0-9_.-]*(/[A-Za-z0-9][A-Za-z0-9_.-]*)?$|^[^@\s]+@[^@\s]+$"
)

STEPS: tuple[tuple[str, str], ...] = (
    ("register", "Register project"),
    ("repository", "Create GitHub repository"),
    ("configure_repository", "Configure repository (labels)"),
    ("branch_protection", "Configure branch protection"),
    ("codeowners", "Configure CODEOWNERS"),
    ("cicd", "Configure CI/CD"),
    ("org_policies", "Configure organisation policies"),
    ("environment", "Create environment"),
    ("ai", "Configure AI"),
    ("claude_code", "Configure Claude Code"),
    ("skills", "Configure skills"),
    ("mcp", "Configure MCP"),
    ("knowledge", "Connect knowledge"),
    ("manifest", "Write project manifest"),
)


@dataclass
class _Run:
    org: str
    record: ProjectRecord
    who: Identity
    branch: str = "main"
    notes: dict[str, str] = field(default_factory=dict)


class ProjectService:
    def __init__(
        self,
        store: ProjectStore,
        github: GitHubPort,
        require: Require,
        audit: AuditService,
        guard: OperationGuard,
        policy: PolicyService,
        *,
        default_org: str,
        default_environment: str,
        ai_provider: str,
        ai_model: str,
        instructions: str = DEFAULT_INSTRUCTIONS,
        knowledge_sources: Callable[[], list[str]] = list,
        skills: Callable[[], SkillRegistry | None] = lambda: None,
        mcp: Callable[[], McpRegistry | None] = lambda: None,
        mcp_allowed: Callable[[str], bool] = lambda name: True,
        stratos_version: str = "0.0.0",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._gh = github
        self._require = require
        self._audit = audit
        self._guard = guard
        self._policy = policy
        self._org = default_org
        self._default_env = default_environment
        self._ai_provider = ai_provider
        self._ai_model = ai_model
        self._instructions = instructions
        self._knowledge_sources = knowledge_sources
        self._skills = skills
        self._mcp = mcp
        self._mcp_allowed = mcp_allowed
        self._version = stratos_version
        self._clock = clock
        self._redactor = SecretRedactor()
        self._handlers: dict[str, Callable[[_Run], str]] = {
            "register": lambda run: "recorded in the registry",
            "repository": self._step_repository,
            "configure_repository": self._step_labels,
            "branch_protection": self._step_protection,
            "codeowners": self._step_codeowners,
            "cicd": self._step_cicd,
            "org_policies": self._step_policies,
            "environment": self._step_environment,
            "ai": self._step_ai,
            "claude_code": self._step_claude,
            "skills": self._step_skills,
            "mcp": self._step_mcp,
            "knowledge": self._step_knowledge,
            "manifest": self._step_manifest,
        }

    # ---- read ----------------------------------------------------------------------------
    def list_projects(self, org: str | None = None) -> list[ProjectRecord]:
        self._require(Permission.ORG_READ)
        return self._store.list(validate_name(org, "organisation") if org else self._org)

    def get(self, name: str, org: str | None = None) -> ProjectRecord:
        self._require(Permission.ORG_READ)
        org = validate_name(org or self._org, "organisation")
        record = self._store.get(org, validate_name(name, "project name"))
        if record is None:
            raise ResourceNotFoundError(f"Project {org}/{name} was not found.")
        return record

    # ---- validation ----------------------------------------------------------------------
    def _validate(self, spec: ProjectSpec) -> None:
        validate_name(spec.name, "project name")
        if spec.template not in TEMPLATES:
            raise ValidationError(
                f"Unknown template '{spec.template}'.",
                hint=f"Choose one of: {', '.join(TEMPLATES)}.",
            )
        for owner in spec.owners:
            if not _OWNER.match(owner):
                raise ValidationError(
                    f"Invalid owner '{owner[:40]}'.",
                    hint="Use @user, @org/team or an email address.",
                )
        for name in (*spec.skills, *spec.mcp_servers):
            validate_slug(name, "name")
        if spec.skills and self._skills() is None:
            raise ConfigurationError(
                "No skills registry is configured.",
                hint="Run `stratos config set skills.registry <dir>`.",
            )
        if spec.mcp_servers and self._mcp() is None:
            raise ConfigurationError(
                "No MCP registry is configured.",
                hint="Run `stratos config set mcp.registry <dir>`.",
            )

    # ---- create --------------------------------------------------------------------------
    def create(self, spec: ProjectSpec, *, org: str | None = None) -> ProjectCreateResult | None:
        """Create (or resume) a project. Returns None in dry-run mode."""
        org = validate_name(org or self._org, "organisation")
        self._validate(spec)
        who = self._require(Permission.PROJECT_CREATE)
        self._require(Permission.REPO_CREATE)
        if spec.skills:
            self._require(Permission.SKILL_INSTALL)
        if spec.mcp_servers:
            self._require(Permission.MCP_INSTALL)
        key = f"{org}/{spec.name}"
        if not spec.private:
            self._check_public(key)
        existing = self._store.get(org, spec.name)
        record = existing or self._new_record(org, spec, who)
        if existing and existing.status is ProjectStatus.ACTIVE:
            return ProjectCreateResult(
                project=existing,
                created=False,
                steps=tuple(StepOutcome(key=k, title=t, status="already") for k, t in STEPS),
            )
        done = set(record.completed_steps)
        plan = [f"{title} [{'already done' if k in done else 'pending'}]" for k, title in STEPS]
        if self._guard.preview("project create", plan):
            return None
        if not record.private:
            self._guard.confirm(
                f"{key} will be created as PUBLIC:", ["Anyone can read its contents"]
            )
        return self._run(record, who, org)

    def _check_public(self, key: str) -> None:
        try:
            self._policy.check_public_repo()
        except AuthorizationError:
            self._audit.emit(AuditAction.PROJECT_CREATE, "project", key, result=AuditResult.DENIED)
            raise

    def _new_record(self, org: str, spec: ProjectSpec, who: Identity) -> ProjectRecord:
        now = self._clock().isoformat()
        return ProjectRecord(
            name=spec.name, org=org, description=spec.description, private=spec.private,
            template=spec.template, owners=spec.owners, skills=spec.skills,
            mcp_servers=spec.mcp_servers, created_by=who.display, created_at=now, updated_at=now,
        )  # fmt: skip

    def _run(self, record: ProjectRecord, who: Identity, org: str) -> ProjectCreateResult:
        run = _Run(org=org, record=record, who=who, branch=record.default_branch)
        outcomes: list[StepOutcome] = []
        details = {"template": record.template, "private": str(record.private)}
        with self._audit.record(AuditAction.PROJECT_CREATE, "project", record.key, details):
            self._store.save(run.record)
            for key, title in STEPS:
                if key in run.record.completed_steps:
                    outcomes.append(StepOutcome(key=key, title=title, status="already"))
                    continue
                try:
                    detail = self._handlers[key](run)
                except (StratosError, OSError) as exc:
                    message = self._redactor.redact_text(getattr(exc, "message", str(exc)))[:300]
                    run.record = self._touch(
                        run.record, status=ProjectStatus.PARTIAL, last_error=f"{title}: {message}"
                    )
                    self._store.save(run.record)
                    raise
                status = "skipped" if detail.startswith("skipped") else "done"
                outcomes.append(StepOutcome(key=key, title=title, status=status, detail=detail))
                run.record = self._touch(
                    run.record, completed_steps=(*run.record.completed_steps, key), last_error=None
                )
                self._store.save(run.record)
            run.record = self._touch(run.record, status=ProjectStatus.ACTIVE)
            self._store.save(run.record)
        return ProjectCreateResult(project=run.record, created=True, steps=tuple(outcomes))

    def _touch(self, record: ProjectRecord, **changes: Any) -> ProjectRecord:
        return record.model_copy(update={**changes, "updated_at": self._clock().isoformat()})

    # ---- steps ---------------------------------------------------------------------------
    def _repo_name(self, run: _Run) -> str:
        return run.record.name

    def _step_repository(self, run: _Run) -> str:
        rec = run.record
        existing = self._gh.get_repo(run.org, rec.name)
        if existing is None:
            try:
                existing = self._gh.create_repo(
                    run.org, rec.name, private=rec.private, description=rec.description or None
                )
                verb = "created"
            except ValidationError:  # lost a race: someone created it in between
                existing = self._gh.get_repo(run.org, rec.name)
                if existing is None:
                    raise
                verb = "already existed"
        else:
            verb = "already existed"
        run.branch = existing.default_branch
        run.record = self._touch(
            rec, repository=existing.full_name, default_branch=existing.default_branch
        )
        return f"{existing.full_name} {verb}"

    def _step_labels(self, run: _Run) -> str:
        created, updated = self._gh.upsert_labels(run.org, run.record.name, DEFAULT_LABELS)
        return f"labels: {created} created, {updated} updated"

    def _step_protection(self, run: _Run) -> str:
        self._gh.protect_branch(run.org, run.record.name, run.branch)
        return f"branch '{run.branch}' protected"

    def _step_codeowners(self, run: _Run) -> str:
        if not run.record.owners:
            return "skipped: no owners given"
        content = "* " + " ".join(run.record.owners) + "\n"
        changed = self._gh.put_codeowners(run.org, run.record.name, content, run.branch)
        return "CODEOWNERS written" if changed else "CODEOWNERS already up to date"

    def _step_cicd(self, run: _Run) -> str:
        template = CI_TEMPLATES.get(run.record.template)
        if template is None:
            return "skipped: no CI template selected"
        if self._gh.get_file(run.org, run.record.name, CI_PATH, ref=run.branch) is not None:
            return "skipped: a CI workflow already exists (left untouched)"
        self._gh.put_file(
            run.org, run.record.name, CI_PATH, template, branch=run.branch,
            message="chore: add CI workflow (Stratos)",
        )  # fmt: skip
        return f"{run.record.template} CI workflow added"

    def _step_policies(self, run: _Run) -> str:
        self._gh.apply_repo_policy(run.org, run.record.name)
        results = self._gh.apply_security(run.org, run.record.name)
        return "merge policy applied; " + "; ".join(results)

    def _step_environment(self, run: _Run) -> str:
        name = self._default_env
        env = self._gh.get_environment(run.org, run.record.name, name)
        if env is None:
            self._gh.create_environment(
                run.org, run.record.name, name, protected=name == "production"
            )
        envs = tuple(dict.fromkeys((*run.record.environments, name)))
        run.record = self._touch(run.record, environments=envs)
        return f"environment '{name}' " + ("ready" if env else "created")

    def _step_ai(self, run: _Run) -> str:
        self._policy.check_provider(self._ai_provider)
        self._policy.check_model(self._ai_model)
        run.record = self._touch(run.record, ai_provider=self._ai_provider, ai_model=self._ai_model)
        return f"{self._ai_provider} / {self._ai_model}"

    def _put_managed(self, run: _Run, path: str, block_id: str, content: str, message: str) -> bool:
        current = self._gh.get_file(run.org, run.record.name, path, ref=run.branch) or ""
        return self._gh.put_file(
            run.org, run.record.name, path, set_managed_block(current, block_id, content),
            branch=run.branch, message=message,
        )  # fmt: skip

    def _step_claude(self, run: _Run) -> str:
        changed = self._put_managed(
            run, "CLAUDE.md", "instructions",
            "## Organisation instructions\n\n" + self._instructions.strip(),
            "chore: add organisation instructions for Claude Code (Stratos)",
        )  # fmt: skip
        return "CLAUDE.md instructions " + ("added" if changed else "already current")

    def _step_skills(self, run: _Run) -> str:
        names = run.record.skills
        if not names:
            return "skipped: no skills requested"
        registry = self._skills()
        assert registry is not None
        for name in names:
            manifest = registry.get(name)
            if manifest is None:
                raise ResourceNotFoundError(f"Skill '{name}' is not in the registry.")
            check_compatibility(manifest, self._version)
            files = registry.files(name)
            if compute_checksum(files) != manifest.checksum:
                raise ValidationError(f"Checksum mismatch for skill '{name}'; it was not added.")
            for rel, data in files.items():
                target = f".claude/skills/{name}/{safe_relative(rel).as_posix()}"
                self._gh.put_file(
                    run.org, run.record.name, target, data, branch=run.branch,
                    message=f"chore: add skill {name} (Stratos)",
                )  # fmt: skip
        return f"{len(names)} skill(s) committed to .claude/skills"

    def _step_mcp(self, run: _Run) -> str:
        names = run.record.mcp_servers
        if not names:
            return "skipped: no MCP servers requested"
        registry = self._mcp()
        assert registry is not None
        servers: dict[str, Any] = {}
        for name in names:
            spec = registry.get(name)
            if spec is None:
                raise ResourceNotFoundError(f"MCP server '{name}' is not in the registry.")
            if not self._mcp_allowed(name):
                raise AuthorizationError(
                    f"MCP server '{name}' is not permitted by organisation policy."
                )
            validate_server(spec)  # secrets never reach shared configuration
            servers[name] = to_config(spec)
        current = self._gh.get_file(run.org, run.record.name, ".mcp.json", ref=run.branch)
        try:
            base = json.loads(current) if current else {}
        except ValueError as exc:
            raise ConfigurationError(
                ".mcp.json in the repository is not valid JSON; left untouched."
            ) from exc
        merged = deep_merge(base if isinstance(base, dict) else {}, {"mcpServers": servers})
        text = json.dumps(merged, indent=2, sort_keys=True) + "\n"
        self._gh.put_file(
            run.org, run.record.name, ".mcp.json", text,
            branch=run.branch, message="chore: add MCP servers (Stratos)",
        )  # fmt: skip
        return f"{len(names)} MCP server(s) configured"

    def _step_knowledge(self, run: _Run) -> str:
        sources = self._knowledge_sources()
        changed = self._put_managed(
            run, "CLAUDE.md", "knowledge", knowledge_block(sources),
            "chore: explain Stratos knowledge to Claude Code",
        )  # fmt: skip
        return f"{len(sources)} knowledge source(s) " + (
            "connected" if changed else "already connected"
        )

    def _step_manifest(self, run: _Run) -> str:
        rec = run.record
        manifest = {
            "name": rec.name, "organisation": rec.org, "repository": rec.repository,
            "template": rec.template, "owners": list(rec.owners),
            "environments": list(rec.environments),
            "skills": list(rec.skills), "mcp_servers": list(rec.mcp_servers),
            "ai": {"provider": rec.ai_provider, "model": rec.ai_model},
        }  # fmt: skip
        changed = self._gh.put_file(
            run.org, rec.name, ".stratos/project.yaml", yaml.safe_dump(manifest, sort_keys=False),
            branch=run.branch, message="chore: add Stratos project manifest",
        )  # fmt: skip
        return "manifest " + ("written" if changed else "already current")

    # ---- update --------------------------------------------------------------------------
    def update(
        self,
        name: str,
        *,
        description: str | None = None,
        owners: tuple[str, ...] | None = None,
        org: str | None = None,
    ) -> ProjectRecord | None:
        org = validate_name(org or self._org, "organisation")
        self._require(Permission.PROJECT_UPDATE)
        record = self._store.get(org, validate_name(name, "project name"))
        if record is None:
            raise ResourceNotFoundError(f"Project {org}/{name} was not found.")
        for owner in owners or ():
            if not _OWNER.match(owner):
                raise ValidationError(f"Invalid owner '{owner[:40]}'.")
        steps = []
        if description is not None:
            steps.append("Update the repository description")
        if owners is not None:
            steps.append("Rewrite CODEOWNERS")
        if not steps:
            raise ValidationError("Nothing to update.", hint="Use --description and/or --owners.")
        if self._guard.preview("project update", steps):
            return None
        with self._audit.record(AuditAction.PROJECT_UPDATE, "project", record.key):
            changes: dict[str, Any] = {}
            if description is not None:
                self._gh.update_repo(org, record.name, description=description)
                changes["description"] = description
            if owners is not None:
                content = "* " + " ".join(owners) + "\n" if owners else ""
                if owners:
                    self._gh.put_codeowners(org, record.name, content, record.default_branch)
                changes["owners"] = owners
            record = self._touch(record, **changes)
            self._store.save(record)
        return record

    # ---- delete --------------------------------------------------------------------------
    def delete(
        self, name: str, *, archive_repo: bool = False, org: str | None = None
    ) -> ProjectRecord | None:
        org = validate_name(org or self._org, "organisation")
        self._require(Permission.PROJECT_DELETE)
        if archive_repo:
            self._require(Permission.REPO_ARCHIVE)
        record = self._store.get(org, validate_name(name, "project name"))
        if record is None:
            raise ResourceNotFoundError(f"Project {org}/{name} was not found.")
        impact = [f"{record.key} is removed from the project registry"]
        impact.append(
            "The GitHub repository is ARCHIVED (read-only)"
            if archive_repo
            else "The GitHub repository is kept"
        )
        if self._guard.preview("project delete", impact):
            return None
        self._guard.confirm_destructive("Deleting", impact)
        with self._audit.record(AuditAction.PROJECT_DELETE, "project", record.key):
            if archive_repo and record.repository:
                self._gh.archive_repo(org, record.name)
            self._store.delete(org, record.name)
        return record
