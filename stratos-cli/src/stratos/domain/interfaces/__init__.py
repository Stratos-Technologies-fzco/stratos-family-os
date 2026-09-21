"""Ports (Protocols) that services depend on and adapters implement."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class Renderer(Protocol):
    """Port for all user-facing output (implemented by cli/output.py)."""

    def data(
        self,
        rows: Sequence[Mapping[str, Any]] | Mapping[str, Any],
        *,
        title: str | None = None,
    ) -> None: ...

    def success(self, message: str) -> None: ...

    def info(self, message: str) -> None: ...

    def warning(self, message: str) -> None: ...

    def error(self, message: str, *, hint: str | None = None) -> None: ...

    def debug(self, message: str) -> None: ...

    def confirm(self, message: str, *, default: bool = False) -> bool: ...


from stratos.domain.interfaces.audit import AuditStore  # noqa: E402
from stratos.domain.interfaces.extensions import (  # noqa: E402
    AgentRegistry,
    AgentRunLog,
    AIProvider,
    KnowledgeProvider,
    McpRegistry,
    SkillRegistry,
)
from stratos.domain.interfaces.github import GitHubPort  # noqa: E402
from stratos.domain.interfaces.identity import (  # noqa: E402
    AccessTokenProvider,
    CallbackReceiver,
    IdentityProvider,
    SecretStore,
)
from stratos.domain.interfaces.services import (  # noqa: E402
    BackupCatalog,
    Cache,
    ClaudeProject,
    FileProtector,
    McpToolClient,
)
from stratos.domain.interfaces.stores import ProjectStore, WorkspaceStore  # noqa: E402

__all__ = [
    "AIProvider",
    "AccessTokenProvider",
    "AgentRegistry",
    "AgentRunLog",
    "AuditStore",
    "BackupCatalog",
    "Cache",
    "CallbackReceiver",
    "ClaudeProject",
    "FileProtector",
    "GitHubPort",
    "IdentityProvider",
    "KnowledgeProvider",
    "McpRegistry",
    "McpToolClient",
    "ProjectStore",
    "Renderer",
    "SecretStore",
    "SkillRegistry",
    "WorkspaceStore",
]
