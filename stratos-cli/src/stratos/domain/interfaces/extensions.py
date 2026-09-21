"""Ports for AI providers, knowledge sources, registries and Claude Code."""

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Protocol

from stratos.domain.models.extensions import (
    AgentDefinition,
    AgentRunRecord,
    AIResponse,
    ChatMessage,
    ChatTurn,
    KnowledgeDocument,
    KnowledgeHit,
    KnowledgeStatus,
    McpServerSpec,
    ModelInfo,
    SkillManifest,
    ToolSpec,
)


class AIProvider(Protocol):
    """Vendor-neutral AI provider. The CLI never imports a vendor SDK directly."""

    name: str

    async def ask(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> AIResponse: ...

    def stream(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]: ...

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] = (),
        model: str | None = None,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> ChatTurn:
        """One model turn that may request tool calls (used by the agent runner)."""
        ...

    async def list_models(self) -> list[ModelInfo]: ...


class KnowledgeProvider(Protocol):
    name: str

    async def search(self, query: str, *, limit: int = 5) -> list[KnowledgeHit]: ...

    async def get(self, doc_id: str) -> KnowledgeDocument | None: ...

    async def sync(self) -> KnowledgeStatus: ...

    async def status(self) -> KnowledgeStatus: ...


class SkillRegistry(Protocol):
    def list(self) -> list[SkillManifest]: ...

    def get(self, name: str) -> SkillManifest | None: ...

    def files(self, name: str) -> dict[str, bytes]:
        """Relative path -> content for one skill (used for checksum and install)."""
        ...


class McpRegistry(Protocol):
    def list(self) -> list[McpServerSpec]: ...

    def get(self, name: str) -> McpServerSpec | None: ...


class AgentRegistry(Protocol):
    def list(self) -> list[AgentDefinition]: ...

    def get(self, name: str) -> AgentDefinition | None: ...

    def save(self, agent: AgentDefinition) -> Path: ...


class AgentRunLog(Protocol):
    def append(self, record: AgentRunRecord) -> None: ...

    def recent(self, agent: str, *, limit: int = 10) -> list[AgentRunRecord]: ...
