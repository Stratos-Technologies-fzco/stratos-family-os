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
