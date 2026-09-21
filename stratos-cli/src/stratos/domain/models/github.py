"""GitHub-related domain models (repositories, teams, pull requests, ...)."""

from stratos.domain.models import StratosModel


class Repository(StratosModel):
    name: str
    full_name: str
    private: bool = True
    default_branch: str = "main"
    description: str | None = None
    url: str = ""
    archived: bool = False


class OrgInfo(StratosModel):
    login: str
    name: str | None = None
    description: str | None = None
    members_can_create_repositories: bool | None = None
    default_repository_permission: str | None = None
    two_factor_requirement_enabled: bool | None = None


class Member(StratosModel):
    login: str
    role: str | None = None


class Team(StratosModel):
    slug: str
    name: str
    description: str | None = None
    privacy: str | None = None
    permission: str | None = None


class TeamRepoPermission(StratosModel):
    repository: str
    permission: str


class RepoCreateResult(StratosModel):
    repository: Repository
    created: bool  # False when the repository already existed (idempotent re-run)


class Branch(StratosModel):
    name: str
    protected: bool = False


class PullRequest(StratosModel):
    number: int
    title: str
    state: str
    author: str = ""
    head: str = ""
    base: str = ""
    draft: bool = False
    url: str = ""


class Issue(StratosModel):
    number: int
    title: str
    state: str
    author: str = ""
    labels: tuple[str, ...] = ()
    url: str = ""


class Label(StratosModel):
    name: str
    color: str = "ededed"  # 6-digit hex, no '#'
    description: str = ""


class Workflow(StratosModel):
    id: int
    name: str
    state: str
    path: str = ""


class WorkflowRun(StratosModel):
    id: int
    name: str
    status: str
    conclusion: str | None = None
    branch: str = ""
    url: str = ""
