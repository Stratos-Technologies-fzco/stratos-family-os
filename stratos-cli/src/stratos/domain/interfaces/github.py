"""Port for everything the application layer needs from GitHub."""

from typing import Protocol

from stratos.domain.models.github import (
    Branch,
    Issue,
    Label,
    Member,
    OrgInfo,
    PullRequest,
    Repository,
    Team,
    TeamRepoPermission,
    Workflow,
    WorkflowRun,
)
from stratos.domain.models.workflow import DeploymentInfo, DeploymentStatus, EnvironmentInfo


class GitHubPort(Protocol):
    """Everything the application layer needs from GitHub. Commands never see GitHub details."""

    def get_repo(self, org: str, name: str) -> Repository | None: ...

    def create_repo(
        self, org: str, name: str, *, private: bool = True, description: str | None = None
    ) -> Repository: ...

    def list_repos(self, org: str, *, limit: int = 100) -> list[Repository]: ...

    def archive_repo(self, org: str, name: str) -> Repository: ...

    def protect_branch(self, org: str, repo: str, branch: str) -> None: ...

    def put_codeowners(self, org: str, repo: str, content: str, branch: str) -> bool:
        """Create or update CODEOWNERS. Returns False when it was already up to date."""
        ...

    def get_org(self, org: str) -> OrgInfo: ...

    def list_members(self, org: str, *, limit: int = 200) -> list[Member]: ...

    def list_teams(self, org: str, *, limit: int = 200) -> list[Team]: ...

    def get_team(self, org: str, slug: str) -> Team | None: ...

    def team_members(self, org: str, slug: str, *, limit: int = 200) -> list[Member]: ...

    def team_repos(self, org: str, slug: str, *, limit: int = 200) -> list[TeamRepoPermission]: ...

    # ---- branches, pull requests, issues, labels, Actions, policy and security -----------
    def list_branches(self, org: str, repo: str, *, limit: int = 100) -> list[Branch]: ...

    def list_pull_requests(
        self, org: str, repo: str, *, state: str = "open", limit: int = 50
    ) -> list[PullRequest]: ...

    def list_issues(
        self, org: str, repo: str, *, state: str = "open", limit: int = 50
    ) -> list[Issue]: ...

    def list_labels(self, org: str, repo: str) -> list[Label]: ...

    def upsert_labels(self, org: str, repo: str, desired: list[Label]) -> tuple[int, int]:
        """Create missing and update changed labels (never deletes): (created, updated)."""
        ...

    def list_workflows(self, org: str, repo: str) -> list[Workflow]: ...

    def list_workflow_runs(self, org: str, repo: str, *, limit: int = 20) -> list[WorkflowRun]: ...

    def configure_actions(
        self, org: str, repo: str, *, allowed_actions: str = "selected"
    ) -> None: ...

    def apply_repo_policy(self, org: str, repo: str) -> None:
        """Standard merge policy: squash only, delete branches on merge."""
        ...

    def apply_security(self, org: str, repo: str) -> list[str]:
        """Secret scanning, push protection, vulnerability alerts, security updates.
        Returns one line per feature (applied or unavailable)."""
        ...

    # ---- files, description, environments and deployments --------------------------------
    def update_repo(self, org: str, name: str, *, description: str) -> Repository: ...

    def get_file(self, org: str, repo: str, path: str, *, ref: str | None = None) -> str | None:
        """Decoded text of a file, or None when it does not exist."""
        ...

    def put_file(
        self, org: str, repo: str, path: str, content: str | bytes, *, branch: str, message: str
    ) -> bool:
        """Create or update a file. Returns False when it was already identical."""
        ...

    def resolve_ref(self, org: str, repo: str, ref: str) -> str:
        """Commit SHA for a branch, tag or SHA."""
        ...

    def create_environment(
        self, org: str, repo: str, name: str, *, protected: bool = False
    ) -> EnvironmentInfo: ...

    def list_environments(self, org: str, repo: str) -> list[EnvironmentInfo]: ...

    def get_environment(self, org: str, repo: str, name: str) -> EnvironmentInfo | None: ...

    def delete_environment(self, org: str, repo: str, name: str) -> None: ...

    def create_deployment(
        self,
        org: str,
        repo: str,
        *,
        ref: str,
        environment: str,
        description: str = "",
        payload: dict[str, object] | None = None,
    ) -> DeploymentInfo: ...

    def list_deployments(
        self, org: str, repo: str, *, environment: str | None = None, limit: int = 10
    ) -> list[DeploymentInfo]:
        """Newest first."""
        ...

    def deployment_statuses(
        self, org: str, repo: str, deployment_id: int
    ) -> list[DeploymentStatus]:
        """Newest first."""
        ...

    def add_deployment_status(
        self, org: str, repo: str, deployment_id: int, state: str, *, description: str = ""
    ) -> None: ...
