"""Models for projects, workspaces, environments and deployments."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from stratos.domain.enums import ProjectStatus
from stratos.domain.models import StratosModel


class ProjectSpec(StratosModel):
    """What the user asks for when creating a project."""

    name: str
    description: str = ""
    private: bool = True
    template: str = "none"  # python | node | none
    owners: tuple[str, ...] = ()  # CODEOWNERS entries, e.g. @acme/platform-team
    skills: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()


class ProjectRecord(StratosModel):
    """Registry entry. `completed_steps` makes creation resumable after a partial failure."""

    name: str
    org: str
    repository: str = ""  # org/name once created
    default_branch: str = "main"
    description: str = ""
    private: bool = True
    template: str = "none"
    owners: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    environments: tuple[str, ...] = ()
    ai_provider: str = ""
    ai_model: str = ""
    status: ProjectStatus = ProjectStatus.PROVISIONING
    completed_steps: tuple[str, ...] = ()
    last_error: str | None = None
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def key(self) -> str:
        return f"{self.org}/{self.name}"


class StepOutcome(StratosModel):
    key: str
    title: str
    status: str  # done | already | skipped | pending | failed
    detail: str = ""


class ProjectCreateResult(StratosModel):
    project: ProjectRecord
    created: bool  # False when the project was already fully provisioned
    steps: tuple[StepOutcome, ...] = ()


class WorkspaceRecord(StratosModel):
    name: str
    project: str  # org/name
    repository: str = ""
    path: str
    components: tuple[str, ...] = ()
    status: str = "ready"
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""


class EnvironmentInfo(StratosModel):
    name: str
    url: str = ""
    protected: bool = False
    created_at: str = ""


class DeploymentInfo(StratosModel):
    id: int
    ref: str
    sha: str
    environment: str
    creator: str = ""
    created_at: str = ""
    description: str = ""
    state: str = "unknown"  # latest status: queued, in_progress, success, failure, ...
    payload: dict[str, Any] = {}


class DeploymentStatus(StratosModel):
    state: str
    description: str = ""
    created_at: str = ""


class ProjectManifest(BaseModel):
    """`.stratos/project.yaml` in a project folder. Unknown keys are ignored."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    organisation: str = ""
    repository: str = ""
    template: str = "none"
    owners: tuple[str, ...] = ()
    environments: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    ai: dict[str, str] = {}
