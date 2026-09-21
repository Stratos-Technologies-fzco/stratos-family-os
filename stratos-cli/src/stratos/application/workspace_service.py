"""Workspaces (M21): provision and connect a developer's local working copy of a project.

Create is idempotent and resumable: an existing clone is reused, an existing `.env` or git hook is
never overwritten, and dependency installation only happens when explicitly requested
(installing packages runs third-party code).
"""

import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stratos.application.audit_service import AuditService
from stratos.application.init_service import InitService
from stratos.application.safety import OperationGuard
from stratos.domain.enums import AuditAction, Permission
from stratos.domain.exceptions import (
    DependencyError,
    ResourceNotFoundError,
    StratosError,
    ValidationError,
)
from stratos.domain.interfaces import ProjectStore, WorkspaceStore
from stratos.domain.models.auth import Identity
from stratos.domain.models.workflow import WorkspaceRecord
from stratos.domain.standards import HOOK_MARKER, PRE_COMMIT_HOOK
from stratos.utils.redaction import SecretRedactor
from stratos.utils.validation import validate_name

Require = Callable[[Permission], Identity]
Runner = Callable[[list[str], Path], tuple[int, str]]


def default_runner(cmd: list[str], cwd: Path) -> tuple[int, str]:
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=900, check=False)
    return out.returncode, (out.stdout + out.stderr).strip()


def venv_python(root: Path) -> Path:
    scripts = root / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    return scripts / ("python.exe" if os.name == "nt" else "python")


@dataclass(frozen=True)
class ComponentResult:
    name: str
    status: str  # done | already | skipped
    detail: str = ""


@dataclass(frozen=True)
class ConnectInfo:
    record: WorkspaceRecord
    enter: str  # command to enter the folder
    activate: str | None  # command to activate the Python environment, if any
    problems: list[str] = field(default_factory=list)


class WorkspaceService:
    def __init__(
        self,
        store: WorkspaceStore,
        projects: ProjectStore,
        require: Require,
        audit: AuditService,
        guard: OperationGuard,
        *,
        root: Path,
        default_org: str,
        clone: Callable[[str, str, Path], Path],
        init_factory: Callable[[Path], InitService],
        run: Runner = default_runner,
        which: Callable[[str], str | None] = shutil.which,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._projects = projects
        self._require = require
        self._audit = audit
        self._guard = guard
        self._root = root
        self._org = default_org
        self._clone = clone
        self._init = init_factory
        self._run = run
        self._which = which
        self._clock = clock
        self._redactor = SecretRedactor()

    # ---- read ----------------------------------------------------------------------------
    def list_workspaces(self) -> list[WorkspaceRecord]:
        self._require(Permission.ORG_READ)
        return self._store.list()

    def get(self, name: str) -> WorkspaceRecord:
        self._require(Permission.ORG_READ)
        record = self._store.get(validate_name(name, "workspace name"))
        if record is None:
            raise ResourceNotFoundError(f"Workspace '{name}' was not found.")
        return record

    # ---- create --------------------------------------------------------------------------
    def create(
        self,
        name: str,
        project: str,
        *,
        path: Path | None = None,
        python: bool = False,
        node: bool = False,
        hooks: bool = True,
        install_deps: bool = False,
        skills: tuple[str, ...] = (),
        mcp: tuple[str, ...] = (),
        org: str | None = None,
    ) -> list[ComponentResult] | None:
        """Create or reconcile a workspace. Returns None in dry-run mode."""
        name = validate_name(name, "workspace name")
        org = validate_name(org or self._org, "organisation")
        who = self._require(Permission.WORKSPACE_CREATE)
        record = self._projects.get(org, validate_name(project, "project name"))
        if record is None or not record.repository:
            raise ResourceNotFoundError(
                f"Project {org}/{project} is not registered.",
                hint="Create it with `stratos project create` first.",
            )
        dest = (path or self._root / name).expanduser().resolve()
        existing = self._store.get(name)
        if existing and existing.project != record.key:
            raise ValidationError(
                f"Workspace '{name}' already belongs to project {existing.project}.",
                hint="Choose another workspace name.",
            )
        components = self._plan(python, node, hooks, install_deps)
        if self._guard.preview(
            "workspace create", [f"{name}: {c}" for c in components] + [f"in {dest}"]
        ):
            return None
        results: list[ComponentResult] = []
        with self._audit.record(AuditAction.WORKSPACE_CREATE, "workspace", name):
            results.append(self._step_clone(org, record.name, record.repository, dest))
            results.append(self._step_init(dest, name, skills, mcp))
            results.append(self._step_python(dest, python, install_deps))
            results.append(self._step_node(dest, node, install_deps))
            results.append(self._step_env_file(dest))
            results.append(self._step_hooks(dest, hooks))
            results.append(self._step_ci(dest))
            now = self._clock().isoformat()
            self._store.save(
                WorkspaceRecord(
                    name=name,
                    project=record.key,
                    repository=record.repository,
                    path=str(dest),
                    components=tuple(r.name for r in results if r.status != "skipped"),
                    status="ready",
                    created_by=who.display,
                    created_at=existing.created_at if existing else now,
                    updated_at=now,
                )
            )
        return results

    @staticmethod
    def _plan(python: bool, node: bool, hooks: bool, install: bool) -> list[str]:
        plan = [
            "clone the repository (an existing clone is reused)",
            "apply the Stratos setup (init)",
        ]
        if python:
            plan.append(
                "create a Python virtual environment"
                + (" and install dependencies" if install else "")
            )
        if node:
            plan.append(
                "check Node.js"
                + (" and install dependencies (no install scripts)" if install else "")
            )
        plan.append("create .env from .env.example if missing (never overwritten)")
        if hooks:
            plan.append("install a pre-commit hook that blocks secrets")
        return plan

    # ---- steps ---------------------------------------------------------------------------
    def _git(self, cwd: Path, *args: str) -> str:
        code, out = self._run(["git", *args], cwd)
        return out if code == 0 else ""

    def _step_clone(self, org: str, name: str, repository: str, dest: Path) -> ComponentResult:
        if (dest / ".git").exists():
            remote = self._git(dest, "remote", "get-url", "origin")
            if repository.lower() not in remote.lower().removesuffix(".git"):
                raise ValidationError(
                    f"{dest} is a git repository but not a clone of {repository}.",
                    hint="Choose another --path or remove the folder yourself.",
                )
            return ComponentResult("repository", "already", f"reusing {dest}")
        if dest.exists() and any(dest.iterdir()):
            raise ValidationError(f"{dest} exists, is not empty and is not a git clone.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._clone(org, name, dest)
        return ComponentResult("repository", "done", f"cloned {repository}")

    def _step_init(
        self, dest: Path, name: str, skills: tuple[str, ...], mcp: tuple[str, ...]
    ) -> ComponentResult:
        items = self._init(dest).init(skills=skills, mcp=mcp)
        changed = [i.name for i in items if i.status in {"created", "updated"}]
        return ComponentResult(
            "claude-and-standards",
            "done" if changed else "already",
            ", ".join(changed) if changed else "already configured",
        )

    def _tool(self, cmd: list[str], cwd: Path, what: str) -> None:
        code, out = self._run(cmd, cwd)
        if code != 0:
            snippet = self._redactor.redact_text(out)[-300:]
            raise DependencyError(f"{what} failed: {snippet}")

    def _step_python(self, dest: Path, wanted: bool, install: bool) -> ComponentResult:
        if not wanted:
            return ComponentResult("python", "skipped")
        created = False
        if not venv_python(dest).exists():
            self._tool(
                [sys.executable, "-m", "venv", ".venv"], dest, "Creating the virtual environment"
            )
            created = True
        detail = "virtual environment " + ("created" if created else "already present")
        if install:
            py = str(venv_python(dest))
            if (dest / "uv.lock").is_file() and self._which("uv"):
                self._tool(["uv", "sync"], dest, "uv sync")
                detail += "; dependencies installed with uv"
            elif (dest / "requirements.txt").is_file():
                self._tool(
                    [py, "-m", "pip", "install", "-r", "requirements.txt"], dest, "pip install"
                )
                detail += "; requirements installed"
            elif (dest / "pyproject.toml").is_file():
                self._tool([py, "-m", "pip", "install", "-e", "."], dest, "pip install")
                detail += "; project installed"
        return ComponentResult("python", "done" if created else "already", detail)

    def _step_node(self, dest: Path, wanted: bool, install: bool) -> ComponentResult:
        if not wanted:
            return ComponentResult("node", "skipped")
        if not self._which("node") or not self._which("npm"):
            raise DependencyError(
                "Node.js and npm are required for --node.", hint="Install Node.js first."
            )
        detail = "Node.js available"
        if install and (dest / "package-lock.json").is_file():
            self._tool(["npm", "ci", "--ignore-scripts"], dest, "npm ci")
            detail += "; dependencies installed (install scripts disabled)"
        return ComponentResult("node", "done", detail)

    def _step_env_file(self, dest: Path) -> ComponentResult:
        example, target = dest / ".env.example", dest / ".env"
        if target.exists():
            return ComponentResult("environment-variables", "already", ".env kept as it is")
        if not example.is_file():
            return ComponentResult("environment-variables", "skipped", "no .env.example")
        shutil.copyfile(example, target)
        return ComponentResult(
            "environment-variables", "done", ".env created from .env.example (git-ignored)"
        )

    def _step_hooks(self, dest: Path, wanted: bool) -> ComponentResult:
        if not wanted:
            return ComponentResult("git-hooks", "skipped")
        hook = dest / ".git" / "hooks" / "pre-commit"
        if hook.exists():
            text = hook.read_text(encoding="utf-8", errors="replace")
            if HOOK_MARKER not in text:
                return ComponentResult(
                    "git-hooks", "skipped", "an existing pre-commit hook was left untouched"
                )
            if text == PRE_COMMIT_HOOK:
                return ComponentResult("git-hooks", "already", "secret-blocking hook installed")
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(PRE_COMMIT_HOOK, encoding="utf-8", newline="\n")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return ComponentResult("git-hooks", "done", "secret-blocking pre-commit hook installed")

    def _step_ci(self, dest: Path) -> ComponentResult:
        workflows = dest / ".github" / "workflows"
        found = sorted(p.name for p in workflows.glob("*.y*ml")) if workflows.is_dir() else []
        return ComponentResult(
            "ci-cd",
            "already" if found else "skipped",
            ", ".join(found) or "no workflows in the repository",
        )

    # ---- connect -------------------------------------------------------------------------
    def connect(self, name: str, *, path: Path | None = None, refresh: bool = False) -> ConnectInfo:
        """Describe (and optionally reconcile or adopt) an existing workspace."""
        self._require(Permission.ORG_READ)
        name = validate_name(name, "workspace name")
        record = self._store.get(name)
        if record is None:
            if path is None:
                raise ResourceNotFoundError(
                    f"Workspace '{name}' was not found.",
                    hint="Use --path to adopt an existing clone.",
                )
            record = self._adopt(name, path)
        dest = Path(record.path)
        problems: list[str] = []
        if not dest.is_dir():
            problems.append(f"folder {dest} does not exist")
        elif not (dest / ".git").exists():
            problems.append("folder is not a git repository")
        if refresh and not problems:
            self.create(
                name,
                record.project.split("/", 1)[1],
                path=dest,
                org=record.project.split("/", 1)[0],
            )
        py = venv_python(dest)
        activate = None
        if py.exists():
            activate = str(py.parent / ("activate.bat" if os.name == "nt" else "activate"))
            if os.name != "nt":
                activate = f"source {activate}"
        enter = f'cd "{dest}"'
        return ConnectInfo(record, enter, activate, problems)

    def _adopt(self, name: str, path: Path) -> WorkspaceRecord:
        self._require(Permission.WORKSPACE_CREATE)
        dest = path.expanduser().resolve()
        if not (dest / ".git").exists():
            raise ValidationError(f"{dest} is not a git repository.")
        remote = self._git(dest, "remote", "get-url", "origin").lower().removesuffix(".git")
        match = next(
            (p for p in self._projects.list() if p.repository and p.repository.lower() in remote),
            None,
        )
        if match is None:
            raise ValidationError(f"{dest} is not a clone of any registered project.")
        now = self._clock().isoformat()
        record = WorkspaceRecord(
            name=name,
            project=match.key,
            repository=match.repository,
            path=str(dest),
            components=("repository",),
            status="ready",
            created_at=now,
            updated_at=now,
        )
        self._store.save(record)
        return record

    # ---- delete --------------------------------------------------------------------------
    def delete(
        self, name: str, *, purge_files: bool = False, force: bool = False
    ) -> WorkspaceRecord | None:
        self._require(Permission.WORKSPACE_DELETE)
        record = self._store.get(validate_name(name, "workspace name"))
        if record is None:
            raise ResourceNotFoundError(f"Workspace '{name}' was not found.")
        dest = Path(record.path)
        impact = [f"Workspace '{name}' is removed from the registry"]
        impact.append(
            f"The folder {dest} is DELETED" if purge_files else f"The folder {dest} is kept"
        )
        if self._guard.preview("workspace delete", impact):
            return None
        if purge_files:
            self._check_purgeable(dest, force)
        self._guard.confirm_destructive("Deleting", impact)
        with self._audit.record(AuditAction.WORKSPACE_DELETE, "workspace", name):
            if purge_files and dest.exists():
                shutil.rmtree(dest, onerror=_make_writable_and_retry)
            self._store.delete(name)
        return record

    def _check_purgeable(self, dest: Path, force: bool) -> None:
        resolved = dest.resolve()
        if (
            resolved == Path(resolved.anchor)
            or resolved == Path.home().resolve()
            or not (resolved / ".git").exists()
        ):
            raise ValidationError(
                f"Refusing to delete {resolved}: it is not a project working copy."
            )
        if not force and self._git(resolved, "status", "--porcelain"):
            raise ValidationError(
                f"{resolved} has uncommitted changes; nothing was deleted.",
                hint="Commit or stash them, or pass --force.",
            )


def _make_writable_and_retry(func: Callable[..., Any], path: str, _exc: object) -> None:
    """rmtree helper: git objects are read-only on Windows."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError as exc:
        raise StratosError(f"Cannot delete {path}: {exc.strerror}") from exc
