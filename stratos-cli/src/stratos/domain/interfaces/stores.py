"""Ports for the project and workspace registries."""

from typing import Protocol

from stratos.domain.models.workflow import ProjectRecord, WorkspaceRecord


class ProjectStore(Protocol):
    """Project registry. Local adapter today; a Platform API adapter can replace it."""

    def get(self, org: str, name: str) -> ProjectRecord | None: ...

    def list(self, org: str | None = None) -> list[ProjectRecord]: ...

    def save(self, record: ProjectRecord) -> None: ...

    def delete(self, org: str, name: str) -> bool: ...


class WorkspaceStore(Protocol):
    def get(self, name: str) -> WorkspaceRecord | None: ...

    def list(self) -> list[WorkspaceRecord]: ...

    def save(self, record: WorkspaceRecord) -> None: ...

    def delete(self, name: str) -> bool: ...
