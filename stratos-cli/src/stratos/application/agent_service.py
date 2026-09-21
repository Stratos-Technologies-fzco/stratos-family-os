"""Agents: define, register, run and inspect under explicit, least-privilege permissions.

The service owns the rules (permission checks, validation, audit, logging); the AgentRunner
performs the bounded, tool-using conversation.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from stratos.application.agent_runner import AgentRunner
from stratos.application.agent_tools import KNOWN_TOOLS
from stratos.application.audit_service import AuditService
from stratos.application.safety import OperationGuard
from stratos.domain.enums import AgentPermission, AuditAction, Permission
from stratos.domain.exceptions import AuthorizationError, ResourceNotFoundError, ValidationError
from stratos.domain.interfaces import AgentRegistry, AgentRunLog
from stratos.domain.models.auth import Identity
from stratos.domain.models.extensions import AgentDefinition, AgentRunRecord
from stratos.utils.validation import sanitize_text, validate_slug

Require = Callable[[Permission], Identity]
MAX_INPUT_CHARS = 100_000


class AgentService:
    def __init__(
        self,
        registry: AgentRegistry,
        run_log: AgentRunLog,
        runner: AgentRunner,
        require: Require,
        audit: AuditService,
        guard: OperationGuard,
        *,
        allowed_permissions: set[AgentPermission],
        check_model: Callable[[str | None], None] = lambda model: None,
        default_model: str = "",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._registry = registry
        self._log = run_log
        self._runner = runner
        self._require = require
        self._audit = audit
        self._guard = guard
        self._allowed = allowed_permissions
        self._check_model = check_model
        self._default_model = default_model
        self._clock = clock

    # ---- read ----------------------------------------------------------------------------
    def list_agents(self) -> list[AgentDefinition]:
        self._require(Permission.ORG_READ)
        return self._registry.list()

    def get(self, name: str) -> AgentDefinition:
        self._require(Permission.ORG_READ)
        agent = self._registry.get(name)
        if agent is None:
            raise ResourceNotFoundError(f"Agent '{name}' was not found.")
        return agent

    def logs(self, name: str, *, limit: int = 10) -> list[AgentRunRecord]:
        self.get(name)
        return self._log.recent(name, limit=limit)

    # ---- create --------------------------------------------------------------------------
    def validate(self, agent: AgentDefinition) -> None:
        validate_slug(agent.name, "agent name")
        for tool in agent.tools:
            spec = KNOWN_TOOLS.get(tool)
            if spec is None:
                raise ValidationError(
                    f"Tool '{tool}' is not available.", hint=f"Available: {', '.join(KNOWN_TOOLS)}."
                )
            if spec.permission not in agent.permissions:
                raise ValidationError(
                    f"Tool '{tool}' requires the '{spec.permission.value}' permission."
                )
        for skill in agent.skills:
            validate_slug(skill, "skill name")
        if agent.mcp_servers and AgentPermission.RUN_COMMANDS not in agent.permissions:
            raise ValidationError(
                "Agents that use MCP servers need the 'run_commands' permission.",
                hint="Add --permission run_commands (an administrator must also allow it).",
            )

    def create(self, agent: AgentDefinition) -> str | None:
        """Register a project agent. Returns the saved path, or None in dry-run mode."""
        self._require(Permission.AGENT_CREATE)
        self.validate(agent)
        if self._registry.get(agent.name) is not None:
            raise ValidationError(f"Agent '{agent.name}' already exists.")
        if self._guard.preview("agent create", [f"Register agent '{agent.name}'"]):
            return None
        with self._audit.record(AuditAction.AGENT_CREATE, "agent", agent.name):
            return str(self._registry.save(agent))

    # ---- run -----------------------------------------------------------------------------
    async def run(self, name: str, task: str) -> AgentRunRecord:
        self._require(Permission.AGENT_RUN)
        agent = self.get(name)
        task = sanitize_text(task)
        if len(task) > MAX_INPUT_CHARS:
            raise ValidationError(
                f"Input is too large ({len(task)} characters; limit {MAX_INPUT_CHARS})."
            )
        with self._audit.record(AuditAction.AGENT_RUN, "agent", name):
            excess = sorted(p.value for p in set(agent.permissions) - self._allowed)
            if excess:
                raise AuthorizationError(
                    f"Agent '{name}' needs permissions that are not allowed here: "
                    f"{', '.join(excess)}.",
                    hint="An administrator can extend `agents.allowed_permissions`.",
                )
            self._check_model(agent.model or self._default_model)
            result = await self._runner.run(agent, task)
            record = AgentRunRecord(
                run_id=uuid.uuid4().hex[:12],
                agent=name,
                started_at=self._clock().isoformat(),
                model=result.model,
                task=task[:2000],
                response=result.text,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                context_documents=tuple(result.context_documents),
                steps=result.steps,
                tools_used=tuple(result.tools_used),
            )
            self._log.append(record)
            return record
