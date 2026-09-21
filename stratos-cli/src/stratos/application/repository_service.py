"""Repository operations. Commands call this; GitHub details stay behind GitHubPort."""

from collections.abc import Callable
from pathlib import Path

from stratos.application.audit_service import AuditService
from stratos.application.policy_service import PolicyService
from stratos.application.safety import OperationGuard
from stratos.domain.enums import AuditAction, AuditResult, Permission
from stratos.domain.exceptions import AuthorizationError, ResourceNotFoundError, ValidationError
from stratos.domain.interfaces.github import GitHubPort
from stratos.domain.models.auth import Identity
from stratos.domain.models.github import (
    Branch,
    Issue,
    Label,
    PullRequest,
    RepoCreateResult,
    Repository,
    Workflow,
    WorkflowRun,
)
from stratos.utils.validation import validate_name

Require = Callable[[Permission], Identity]


class RepositoryService:
    def __init__(
        self,
        github: GitHubPort,
        require: Require,
        audit: AuditService,
        guard: OperationGuard,
        default_org: str,
        *,
        clone: Callable[..., Path],
        policy: PolicyService | None = None,
    ) -> None:
        self._gh = github
        self._require = require
        self._audit = audit
        self._guard = guard
        self._org = default_org
        self._clone = clone
        self._policy = policy

    def _target(self, name: str, org: str | None) -> tuple[str, str]:
        return validate_name(org or self._org, "organisation"), validate_name(
            name, "repository name"
        )

    # ---- create (idempotent) -------------------------------------------------------------
    def create(
        self,
        name: str,
        *,
        private: bool = True,
        description: str | None = None,
        org: str | None = None,
    ) -> RepoCreateResult | None:
        """Create a repository. Safe to re-run: an existing repository is returned unchanged.
        Returns None in dry-run mode."""
        org, name = self._target(name, org)
        self._require(Permission.REPO_CREATE)
        if not private and self._policy is not None:
            try:
                self._policy.check_public_repo()
            except AuthorizationError:
                self._audit.emit(
                    AuditAction.REPO_CREATE,
                    "repository",
                    f"{org}/{name}",
                    result=AuditResult.DENIED,
                )
                raise
        existing = self._gh.get_repo(org, name)
        if self._guard.preview(
            "repo create",
            [f"Repository {org}/{name} already exists; nothing to do"]
            if existing
            else [f"Create {'private' if private else 'PUBLIC'} repository {org}/{name}"],
        ):
            return None
        if not existing and not private:
            self._guard.confirm(
                f"{org}/{name} will be PUBLIC:", ["Anyone on the internet can read its contents"]
            )
        with self._audit.record(AuditAction.REPO_CREATE, "repository", f"{org}/{name}"):
            if existing:
                return RepoCreateResult(repository=existing, created=False)
            try:
                return RepoCreateResult(
                    repository=self._gh.create_repo(
                        org, name, private=private, description=description
                    ),
                    created=True,
                )
            except ValidationError:  # lost a race: someone created it between check and create
                again = self._gh.get_repo(org, name)
                if again is None:
                    raise
                return RepoCreateResult(repository=again, created=False)

    # ---- read ----------------------------------------------------------------------------
    def list_repositories(self, *, org: str | None = None, limit: int = 100) -> list[Repository]:
        self._require(Permission.ORG_READ)
        return self._gh.list_repos(validate_name(org or self._org, "organisation"), limit=limit)

    def get(self, name: str, *, org: str | None = None) -> Repository:
        org, name = self._target(name, org)
        self._require(Permission.ORG_READ)
        repo = self._gh.get_repo(org, name)
        if repo is None:
            raise ResourceNotFoundError(f"Repository {org}/{name} was not found.")
        return repo

    def clone(self, name: str, destination: Path | None = None, *, org: str | None = None) -> Path:
        org, name = self._target(name, org)
        self._require(Permission.ORG_READ)
        return self._clone(org, name, destination or Path(name))

    # ---- configure (idempotent) ----------------------------------------------------------
    def configure(
        self,
        name: str,
        *,
        branch_protection: bool = True,
        codeowners: str | None = None,
        labels: list[Label] | None = None,
        policy: bool = False,
        security: bool = False,
        actions: bool = False,
        org: str | None = None,
    ) -> list[str]:
        """Apply repository settings; every step is idempotent. Returns the actions taken."""
        org, name = self._target(name, org)
        self._require(Permission.REPO_CONFIGURE)
        repo = self.get(name, org=org)
        steps: list[str] = []
        if branch_protection:
            steps.append(f"Protect branch '{repo.default_branch}' (PR review, no force-push)")
        if codeowners:
            steps.append("Set .github/CODEOWNERS")
        if labels:
            steps.append(f"Sync {len(labels)} labels (create or update; none are deleted)")
        if policy:
            steps.append("Apply merge policy (squash only, delete branches on merge)")
        if security:
            steps.append("Enable secret scanning, push protection and vulnerability alerts")
        if actions:
            steps.append("Enable GitHub Actions, restricted to selected actions")
        if not steps:
            raise ValidationError(
                "Nothing to configure.", hint="See `stratos repo configure --help`."
            )
        if self._guard.preview("repo configure", steps):
            return []
        done: list[str] = []
        with self._audit.record(AuditAction.REPO_CONFIGURE, "repository", f"{org}/{name}"):
            if branch_protection:
                self._gh.protect_branch(org, name, repo.default_branch)
                done.append("branch protection applied")
            if codeowners:
                changed = self._gh.put_codeowners(org, name, codeowners, repo.default_branch)
                done.append("CODEOWNERS updated" if changed else "CODEOWNERS already up to date")
            if labels:
                created, updated = self._gh.upsert_labels(org, name, labels)
                done.append(f"labels: {created} created, {updated} updated")
            if policy:
                self._gh.apply_repo_policy(org, name)
                done.append("merge policy applied")
            if security:
                done.extend(self._gh.apply_security(org, name))
            if actions:
                self._gh.configure_actions(org, name)
                done.append("GitHub Actions restricted to selected actions")
        return done

    # ---- read access: branches, pull requests, issues, labels, Actions -------------------
    def branches(self, name: str, *, org: str | None = None) -> list[Branch]:
        org, name = self._target(name, org)
        self._require(Permission.ORG_READ)
        return self._gh.list_branches(org, name)

    def pull_requests(
        self, name: str, *, state: str = "open", limit: int = 50, org: str | None = None
    ) -> list[PullRequest]:
        org, name = self._target(name, org)
        self._require(Permission.ORG_READ)
        return self._gh.list_pull_requests(org, name, state=state, limit=limit)

    def issues(
        self, name: str, *, state: str = "open", limit: int = 50, org: str | None = None
    ) -> list[Issue]:
        org, name = self._target(name, org)
        self._require(Permission.ORG_READ)
        return self._gh.list_issues(org, name, state=state, limit=limit)

    def labels(self, name: str, *, org: str | None = None) -> list[Label]:
        org, name = self._target(name, org)
        self._require(Permission.ORG_READ)
        return self._gh.list_labels(org, name)

    def workflows(self, name: str, *, org: str | None = None) -> list[Workflow]:
        org, name = self._target(name, org)
        self._require(Permission.ORG_READ)
        return self._gh.list_workflows(org, name)

    def workflow_runs(
        self, name: str, *, limit: int = 20, org: str | None = None
    ) -> list[WorkflowRun]:
        org, name = self._target(name, org)
        self._require(Permission.ORG_READ)
        return self._gh.list_workflow_runs(org, name, limit=limit)

    # ---- archive (destructive) -----------------------------------------------------------
    def archive(self, name: str, *, org: str | None = None) -> Repository | None:
        org, name = self._target(name, org)
        self._require(Permission.REPO_ARCHIVE)
        repo = self.get(name, org=org)
        if repo.archived:
            return repo  # already archived: idempotent no-op
        if self._guard.preview("repo archive", [f"Archive {org}/{name} (becomes read-only)"]):
            return None
        self._guard.confirm_destructive(
            "Archiving",
            [f"{org}/{name} becomes read-only", "Pushes, issues and pull requests are blocked"],
        )
        with self._audit.record(AuditAction.REPO_ARCHIVE, "repository", f"{org}/{name}"):
            return self._gh.archive_repo(org, name)
