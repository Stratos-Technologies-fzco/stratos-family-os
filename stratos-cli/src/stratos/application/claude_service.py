"""Claude Code integration: subagents, knowledge and instructions for a project."""

from collections.abc import Callable

from stratos.application.audit_service import AuditService
from stratos.application.safety import OperationGuard
from stratos.domain.enums import AuditAction, Permission
from stratos.domain.exceptions import ResourceNotFoundError
from stratos.domain.interfaces import AgentRegistry, ClaudeProject
from stratos.domain.models.auth import Identity
from stratos.domain.models.files import ClaudeStatus, WriteResult

Require = Callable[[Permission], Identity]


class ClaudeIntegrationService:
    def __init__(
        self,
        claude: ClaudeProject,
        agents: AgentRegistry,
        require: Require,
        audit: AuditService,
        guard: OperationGuard,
    ) -> None:
        self._claude = claude
        self._agents = agents
        self._require = require
        self._audit = audit
        self._guard = guard

    def status(self) -> ClaudeStatus:
        self._require(Permission.ORG_READ)
        return self._claude.detect()

    def sync_agents(self, name: str | None = None) -> list[tuple[str, WriteResult]] | None:
        """Export agents as Claude Code subagents (idempotent). None in dry-run mode."""
        self._require(Permission.CLAUDE_CONFIGURE)
        if name:
            agent = self._agents.get(name)
            if agent is None:
                raise ResourceNotFoundError(f"Agent '{name}' was not found.")
            chosen = [agent]
        else:
            chosen = self._agents.list()
        steps = [f"Write .claude/agents/{a.name}.md" for a in chosen]
        if self._guard.preview("agent sync", steps):
            return None
        results: list[tuple[str, WriteResult]] = []
        for agent in chosen:
            with self._audit.record(AuditAction.CLAUDE_CONFIGURE, "claude-agent", agent.name):
                results.append((agent.name, self._claude.sync_agent(agent)))
        return results

    def connect_knowledge(self, sources: list[str]) -> WriteResult | None:
        self._require(Permission.CLAUDE_CONFIGURE)
        if self._guard.preview(
            "knowledge connect", ["Add a Stratos knowledge section to CLAUDE.md (backed up first)"]
        ):
            return None
        with self._audit.record(AuditAction.CLAUDE_CONFIGURE, "claude-knowledge", "CLAUDE.md"):
            return self._claude.apply_knowledge(sources)

    def apply_instructions(
        self,
        *,
        organisation: str | None = None,
        standards: str | None = None,
        project: str | None = None,
    ) -> WriteResult | None:
        self._require(Permission.CLAUDE_CONFIGURE)
        if self._guard.preview("instructions", ["Update the Stratos section of CLAUDE.md"]):
            return None
        with self._audit.record(AuditAction.CLAUDE_CONFIGURE, "claude-instructions", "CLAUDE.md"):
            return self._claude.apply_instructions(
                organisation=organisation, standards=standards, project=project
            )
