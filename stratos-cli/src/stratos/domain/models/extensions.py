"""Models for skills, MCP servers, AI, knowledge and agents."""

from typing import Any, Literal

from stratos.domain.enums import AgentPermission
from stratos.domain.models import StratosModel


# ---- skills -----------------------------------------------------------------------------
class SkillManifest(StratosModel):
    name: str
    version: str
    description: str = ""
    source: str = ""
    checksum: str  # sha256 of the skill's files (see infrastructure.filesystem.skill_registry)
    permissions: tuple[str, ...] = ()
    compatibility: dict[str, str] = {}  # e.g. {"stratos": ">=0.1.0"}


class InstalledSkill(StratosModel):
    name: str
    version: str
    checksum: str


# ---- MCP --------------------------------------------------------------------------------
class McpServerSpec(StratosModel):
    name: str
    description: str = ""
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = {}  # KEY -> "${ENV_VAR}" reference; never a literal secret
    transport: str = "stdio"
    version: str = ""


class McpToolInfo(StratosModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}


# ---- AI ---------------------------------------------------------------------------------
class ModelInfo(StratosModel):
    id: str
    display_name: str = ""


class AIResponse(StratosModel):
    model: str
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class ToolSpec(StratosModel):
    """A tool the model may call. `name` must be letters, digits, '_' or '-'."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}


class ToolCall(StratosModel):
    id: str
    name: str
    arguments: dict[str, Any] = {}


class ChatMessage(StratosModel):
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()  # on assistant messages
    tool_call_id: str | None = None  # on tool messages


class ChatTurn(StratosModel):
    model: str
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None
    stop_reason: str | None = None


# ---- knowledge --------------------------------------------------------------------------
class KnowledgeHit(StratosModel):
    id: str
    title: str
    score: float
    snippet: str


class KnowledgeDocument(StratosModel):
    id: str
    title: str
    content: str


class KnowledgeStatus(StratosModel):
    provider: str
    documents: int
    last_sync: str | None = None
    stale: int = 0  # files changed since the last sync
    detail: str = ""


# ---- agents -----------------------------------------------------------------------------
class AgentTool(StratosModel):
    name: str
    description: str = ""
    permission: AgentPermission


class AgentDefinition(StratosModel):
    name: str
    description: str = ""
    instructions: str
    model: str | None = None
    tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    knowledge_sources: tuple[str, ...] = ()
    permissions: tuple[AgentPermission, ...] = ()


class AgentRunRecord(StratosModel):
    run_id: str
    agent: str
    started_at: str
    model: str
    task: str
    response: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    context_documents: tuple[str, ...] = ()
    steps: int = 1
    tools_used: tuple[str, ...] = ()
