"""Environments and deployments (M22), backed by GitHub Environments and Deployments.

Stratos records the request (a GitHub deployment plus a `queued` status) and the project's CI
performs the actual rollout; Stratos never claims a deployment succeeded, it reports the statuses
the pipeline posts. Destructive actions (production deploys, rollbacks, environment deletion)
need confirmation and are never retried automatically.
"""

from collections.abc import Callable
from dataclasses import dataclass

from stratos.application.audit_service import AuditService
from stratos.application.safety import OperationGuard
from stratos.domain.enums import AuditAction, Permission
from stratos.domain.exceptions import ResourceNotFoundError, ValidationError
from stratos.domain.interfaces import GitHubPort, ProjectStore
from stratos.domain.models.auth import Identity
from stratos.domain.models.workflow import DeploymentInfo, EnvironmentInfo, ProjectRecord
from stratos.utils.validation import validate_name

Require = Callable[[Permission], Identity]
TARGETS = {"dev": "development", "staging": "staging", "production": "production"}
IN_FLIGHT = {"queued", "pending", "in_progress"}
PROTECTED = {"staging", "production"}


def environment_for(target: str) -> str:
    """`dev` -> development; other names are used as they are."""
    return TARGETS.get(target, target)


@dataclass(frozen=True)
class DeploymentResult:
    deployment: DeploymentInfo
    created: bool  # False when an identical deployment was already in flight


class _Base:
    def __init__(
        self,
        projects: ProjectStore,
        github: GitHubPort,
        require: Require,
        audit: AuditService,
        guard: OperationGuard,
        *,
        default_org: str,
        default_environment: str,
    ) -> None:
        self._projects = projects
        self._gh = github
        self._require = require
        self._audit = audit
        self._guard = guard
        self._org = default_org
        self._default_env = default_environment

    def _project(self, name: str, org: str | None) -> tuple[str, ProjectRecord]:
        org = validate_name(org or self._org, "organisation")
        record = self._projects.get(org, validate_name(name, "project name"))
        if record is None or not record.repository:
            raise ResourceNotFoundError(
                f"Project {org}/{name} is not registered.",
                hint="Create it with `stratos project create`.",
            )
        return org, record

    def _repo(self, record: ProjectRecord) -> str:
        return record.repository.split("/", 1)[1]


class EnvironmentService(_Base):
    def list_environments(self, project: str, org: str | None = None) -> list[EnvironmentInfo]:
        self._require(Permission.ORG_READ)
        org, record = self._project(project, org)
        return self._gh.list_environments(org, self._repo(record))

    def get(self, project: str, name: str | None = None, org: str | None = None) -> EnvironmentInfo:
        self._require(Permission.ORG_READ)
        org, record = self._project(project, org)
        env = environment_for(name or self._default_env)
        found = self._gh.get_environment(org, self._repo(record), env)
        if found is None:
            raise ResourceNotFoundError(f"Environment '{env}' does not exist in {record.key}.")
        return found

    def create(
        self, project: str, name: str | None = None, org: str | None = None
    ) -> tuple[EnvironmentInfo, bool] | None:
        """Create an environment (idempotent). Returns None in dry-run mode."""
        self._require(Permission.ENVIRONMENT_CREATE)
        org, record = self._project(project, org)
        env = environment_for(name or self._default_env)
        existing = self._gh.get_environment(org, self._repo(record), env)
        if existing is not None:
            return existing, False
        if self._guard.preview(
            "environment create", [f"Create environment '{env}' in {record.key}"]
        ):
            return None
        with self._audit.record(
            AuditAction.ENVIRONMENT_CREATE, "environment", f"{record.key}/{env}"
        ):
            created = self._gh.create_environment(
                org, self._repo(record), env, protected=env in PROTECTED
            )
            self._projects.save(
                record.model_copy(
                    update={"environments": tuple(dict.fromkeys((*record.environments, env)))}
                )
            )
        return created, True

    def delete(self, project: str, name: str, org: str | None = None) -> str | None:
        self._require(Permission.ENVIRONMENT_DELETE)
        org, record = self._project(project, org)
        env = environment_for(name)
        if self._gh.get_environment(org, self._repo(record), env) is None:
            raise ResourceNotFoundError(f"Environment '{env}' does not exist in {record.key}.")
        impact = [
            f"Environment '{env}' is deleted from {record.key}",
            "Its deployment history and protection rules are lost",
        ]
        if env == "production":
            impact.insert(0, "THIS IS THE PRODUCTION ENVIRONMENT")
        if self._guard.preview("environment delete", impact):
            return None
        self._guard.confirm_destructive("Deleting", impact)
        with self._audit.record(
            AuditAction.ENVIRONMENT_DELETE, "environment", f"{record.key}/{env}"
        ):
            self._gh.delete_environment(org, self._repo(record), env)
            remaining = tuple(e for e in record.environments if e != env)
            self._projects.save(record.model_copy(update={"environments": remaining}))
        return env


class DeploymentService(_Base):
    def _state(self, org: str, repo: str, info: DeploymentInfo) -> DeploymentInfo:
        statuses = self._gh.deployment_statuses(org, repo, info.id)
        return info.model_copy(update={"state": statuses[0].state if statuses else "pending"})

    # ---- deploy --------------------------------------------------------------------------
    def deploy(
        self, project: str, target: str, *, ref: str | None = None, org: str | None = None
    ) -> DeploymentResult | None:
        """Request a deployment. Returns None in dry-run mode."""
        who = self._require(Permission.DEPLOYMENT_CREATE)
        org, record = self._project(project, org)
        repo = self._repo(record)
        env = environment_for(target)
        wanted_ref = ref or record.default_branch
        sha = self._gh.resolve_ref(org, repo, wanted_ref)
        latest = self._gh.list_deployments(org, repo, environment=env, limit=1)
        if latest:
            current = self._state(org, repo, latest[0])
            if current.sha == sha and current.state in IN_FLIGHT:
                return DeploymentResult(current, created=False)  # identical request already running
        steps = [f"Deploy {record.key} @ {wanted_ref} ({sha[:7]}) to '{env}'"]
        if self._gh.get_environment(org, repo, env) is None:
            steps.insert(0, f"Create environment '{env}'")
        if self._guard.preview("deploy", steps):
            return None
        if env == "production":
            self._guard.confirm("Deploying to PRODUCTION:", steps)
        details = {"ref": wanted_ref, "sha": sha, "environment": env}
        with self._audit.record(
            AuditAction.DEPLOYMENT_CREATE, "deployment", f"{record.key}/{env}", details
        ):
            if self._gh.get_environment(org, repo, env) is None:
                self._gh.create_environment(org, repo, env, protected=env in PROTECTED)
            created = self._gh.create_deployment(
                org,
                repo,
                ref=sha,
                environment=env,
                description=f"Deploy {wanted_ref} to {env}",
                payload={
                    "stratos": {
                        "project": record.key,
                        "ref": wanted_ref,
                        "requested_by": who.display,
                    }
                },
            )
            self._gh.add_deployment_status(
                org, repo, created.id, "queued", description=f"Requested by {who.display}"
            )
        return DeploymentResult(created.model_copy(update={"state": "queued"}), created=True)

    # ---- status --------------------------------------------------------------------------
    def status(
        self, project: str, environment: str | None = None, org: str | None = None
    ) -> list[DeploymentInfo]:
        """Latest deployment (with its current state) for one environment, or for all of them."""
        self._require(Permission.ORG_READ)
        org, record = self._project(project, org)
        repo = self._repo(record)
        names = (
            [environment_for(environment)]
            if environment
            else [e.name for e in self._gh.list_environments(org, repo)] or [self._default_env]
        )
        found: list[DeploymentInfo] = []
        for env in names:
            latest = self._gh.list_deployments(org, repo, environment=env, limit=1)
            if latest:
                found.append(self._state(org, repo, latest[0]))
        return found

    # ---- rollback ------------------------------------------------------------------------
    def rollback(
        self, project: str, environment: str, *, to: int | None = None, org: str | None = None
    ) -> DeploymentInfo | None:
        """Redeploy the previous successful version. Returns None in dry-run mode."""
        who = self._require(Permission.DEPLOYMENT_ROLLBACK)
        org, record = self._project(project, org)
        repo = self._repo(record)
        env = environment_for(environment)
        history = self._gh.list_deployments(org, repo, environment=env, limit=20)
        if not history:
            raise ResourceNotFoundError(f"There are no deployments to '{env}' in {record.key}.")
        current = history[0]
        target = self._pick_target(org, repo, history, to)
        steps = [
            f"'{env}' moves from {current.sha[:7]} ({current.ref}) "
            f"to {target.sha[:7]} ({target.ref})",
            "A new deployment is requested; the pipeline performs the rollout",
        ]
        if self._guard.preview("rollback", steps):
            return None
        self._guard.confirm_destructive("Rolling back", steps)
        details = {"environment": env, "from": current.sha, "to": target.sha}
        with self._audit.record(
            AuditAction.DEPLOYMENT_ROLLBACK, "deployment", f"{record.key}/{env}", details
        ):
            created = self._gh.create_deployment(
                org,
                repo,
                ref=target.sha,
                environment=env,
                description=f"Rollback to {target.sha[:7]}",
                payload={
                    "stratos": {
                        "project": record.key,
                        "rollback_of": current.id,
                        "requested_by": who.display,
                    }
                },
            )
            self._gh.add_deployment_status(
                org, repo, created.id, "queued", description=f"Rollback requested by {who.display}"
            )
        return created.model_copy(update={"state": "queued"})

    def _pick_target(
        self, org: str, repo: str, history: list[DeploymentInfo], to: int | None
    ) -> DeploymentInfo:
        current = history[0]
        if to is not None:
            match = next((d for d in history if d.id == to), None)
            if match is None:
                raise ResourceNotFoundError(f"Deployment {to} was not found in the recent history.")
            return match
        for candidate in history[1:]:
            if candidate.sha == current.sha:
                continue
            if any(
                s.state == "success" for s in self._gh.deployment_statuses(org, repo, candidate.id)
            ):
                return candidate
        raise ValidationError(
            "There is no earlier successful deployment to roll back to.",
            hint="Use --to <deployment id> to choose one explicitly.",
        )
