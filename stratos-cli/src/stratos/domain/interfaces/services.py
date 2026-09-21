"""Ports for the file-system, cache and tool adapters that application services use."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from stratos.domain.models.extensions import AgentDefinition, McpToolInfo
from stratos.domain.models.files import BackupEntry, ClaudeStatus, WriteResult


class Cache(Protocol):
    """Short-lived cache for non-sensitive metadata."""

    def get(self, namespace: str, key: str) -> Any | None: ...

    def set(self, namespace: str, key: str, value: Any, ttl_seconds: float) -> None: ...

    def invalidate(self, namespace: str, key: str | None = None) -> None: ...

    def get_or_load(
        self, namespace: str, key: str, ttl_seconds: float, loader: Callable[[], Any]
    ) -> Any: ...


class FileProtector(Protocol):
    """Protected file writes: backup, merge, validation, rollback (dry-run computes only)."""

    dry_run: bool

    def read_json(self, path: Path) -> dict[str, Any]: ...

    def merge_json(
        self,
        path: Path,
        patch: Mapping[str, Any],
        *,
        validate: Callable[[dict[str, Any]], None] | None = None,
        forbid_secrets: bool = True,
    ) -> WriteResult: ...

    def remove_json_key(self, path: Path, *keys: str) -> WriteResult: ...

    def write_text(self, path: Path, text: str) -> WriteResult: ...

    def write_bytes(self, path: Path, data: bytes) -> WriteResult: ...

    def write_managed_block(self, path: Path, block_id: str, content: str) -> WriteResult: ...

    def backup(self, path: Path) -> Path | None: ...

    def rollback(self, result: WriteResult) -> None: ...


class ClaudeProject(Protocol):
    """A project folder's Claude Code configuration (CLAUDE.md, .mcp.json, skills, agents)."""

    project_dir: Path

    @property
    def settings_path(self) -> Path: ...

    @property
    def skills_dir(self) -> Path: ...

    def detect(self) -> ClaudeStatus: ...

    def apply_instructions(
        self,
        *,
        organisation: str | None = None,
        standards: str | None = None,
        project: str | None = None,
    ) -> WriteResult: ...

    def apply_knowledge(self, sources: list[str]) -> WriteResult: ...

    def merge_settings(self, patch: Mapping[str, Any]) -> WriteResult: ...

    def merge_mcp_servers(
        self,
        servers: Mapping[str, Mapping[str, Any]],
        validate: Callable[[dict[str, Any]], None] | None = None,
    ) -> WriteResult: ...

    def mcp_servers(self) -> dict[str, dict[str, Any]]: ...

    def remove_mcp_server(self, name: str) -> WriteResult: ...

    def rollback(self, result: WriteResult) -> None: ...

    def install_skill(self, name: str, files: Mapping[str, bytes]) -> list[WriteResult]: ...

    def remove_skill(self, name: str) -> Path | None: ...

    def installed_skill_names(self) -> list[str]: ...

    def sync_agent(self, agent: AgentDefinition) -> WriteResult: ...


class McpToolClient(Protocol):
    """A running MCP server an agent can call tools on."""

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def list_tools(self) -> list[McpToolInfo]: ...

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> tuple[str, bool]: ...


class BackupCatalog(Protocol):
    """Finds and reads the backups taken by protected writes."""

    def list(self) -> list[BackupEntry]: ...

    def read(self, entry: BackupEntry) -> bytes: ...

    def restore_skill(self, entry: BackupEntry) -> None:
        """Copy a removed skill's backup folder back. Refuses to overwrite an existing skill."""
        ...
