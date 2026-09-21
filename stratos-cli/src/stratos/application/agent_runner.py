"""AgentRunner: runs one agent as a bounded, tool-using conversation with least privilege.

* Instructions + installed skills (as instructions) form the system prompt.
* Knowledge context is added up front when the agent has `knowledge.search`.
* The model may call the agent's tools; each call is checked against the agent's permissions.
* MCP servers named on the agent are started only if installed, allowed by organisation policy
  and the agent holds `run_commands`; they are always shut down afterwards.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stratos.application.agent_tools import (
    KNOWN_TOOLS,
    MAX_TOOL_OUTPUT,
    RuntimeTool,
    build_file_tools,
    build_knowledge_tool,
    model_safe_name,
)
from stratos.application.knowledge_service import KnowledgeService
from stratos.domain.enums import AgentPermission
from stratos.domain.exceptions import (
    APIError,
    AuthorizationError,
    StratosError,
    ValidationError,
)
from stratos.domain.interfaces import AIProvider
from stratos.domain.models.extensions import AgentDefinition, ChatMessage, ToolSpec
from stratos.infrastructure.mcp.client import McpStdioClient, build_server_env
from stratos.logging import get_logger
from stratos.utils.redaction import SecretRedactor
from stratos.utils.validation import sanitize_text

log = get_logger("agent")
MAX_SKILL_CHARS = 20_000
TOOL_TIMEOUT = 60.0

McpFactory = Callable[[str, dict[str, Any]], McpStdioClient]
SkillLoader = Callable[[str], str | None]


@dataclass
class RunResult:
    text: str
    model: str
    steps: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    context_documents: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)


class AgentRunner:
    def __init__(
        self,
        provider: Callable[[], AIProvider],
        knowledge: KnowledgeService,
        project_dir: Path,
        redactor: SecretRedactor,
        *,
        default_model: str,
        max_tokens: int,
        max_steps: int = 6,
        mcp_configs: Callable[[], Mapping[str, Mapping[str, Any]]] = dict,
        mcp_allowed: Callable[[str], bool] = lambda name: True,
        mcp_factory: McpFactory | None = None,
        environ: Mapping[str, str] | None = None,
        skill_loader: SkillLoader = lambda name: None,
        tool_timeout: float = TOOL_TIMEOUT,
    ) -> None:
        self._provider = provider
        self._knowledge = knowledge
        self._root = project_dir
        self._redactor = redactor
        self._model = default_model
        self._max_tokens = max_tokens
        self._max_steps = max_steps
        self._mcp_configs = mcp_configs
        self._mcp_allowed = mcp_allowed
        self._environ = environ or {}
        self._mcp_factory = mcp_factory or self._default_mcp_factory
        self._skill_loader = skill_loader
        self._tool_timeout = tool_timeout

    def _default_mcp_factory(self, name: str, config: dict[str, Any]) -> McpStdioClient:
        env = build_server_env(config.get("env") or {}, self._environ)
        return McpStdioClient(config["command"], list(config.get("args", [])), env)

    # ---- preparation ---------------------------------------------------------------------
    def _system_prompt(self, agent: AgentDefinition) -> str:
        parts = [agent.instructions]
        for name in agent.skills:
            body = self._skill_loader(name)
            if body is None:
                raise ValidationError(
                    f"Skill '{name}' is not installed.", hint="Run `stratos skill install`."
                )
            parts.append(f'<skill name="{name}">\n{body[:MAX_SKILL_CHARS]}\n</skill>')
        return "\n\n".join(parts)

    def _static_tools(self, agent: AgentDefinition) -> dict[str, RuntimeTool]:
        available = {t.spec.name: t for t in build_file_tools(self._root, self._redactor)}
        available["knowledge_get"] = build_knowledge_tool(self._knowledge)
        chosen: dict[str, RuntimeTool] = {}
        for tool in agent.tools:
            if tool not in KNOWN_TOOLS:
                raise ValidationError(f"Tool '{tool}' is not available.")
            safe = model_safe_name(tool)
            if safe in available:
                chosen[safe] = available[safe]
        return chosen

    async def _start_mcp(
        self, agent: AgentDefinition, stack: list[McpStdioClient]
    ) -> dict[str, RuntimeTool]:
        tools: dict[str, RuntimeTool] = {}
        if not agent.mcp_servers:
            return tools
        if AgentPermission.RUN_COMMANDS not in agent.permissions:
            raise AuthorizationError(
                f"Agent '{agent.name}' lists MCP servers but lacks the 'run_commands' permission."
            )
        configs = self._mcp_configs()
        for server in agent.mcp_servers:
            config = configs.get(server)
            if config is None:
                raise ValidationError(
                    f"MCP server '{server}' is not installed.", hint="Run `stratos mcp install`."
                )
            if not self._mcp_allowed(server):
                raise AuthorizationError(
                    f"MCP server '{server}' is not permitted by organisation policy."
                )
            client = self._mcp_factory(server, dict(config))
            await client.start()
            stack.append(client)
            for info in await client.list_tools():
                name = model_safe_name(f"mcp__{server}__{info.name}")
                tools[name] = RuntimeTool(
                    ToolSpec(
                        name=name, description=info.description, input_schema=info.input_schema
                    ),
                    AgentPermission.RUN_COMMANDS,
                    self._mcp_handler(client, info.name),
                )
        return tools

    @staticmethod
    def _mcp_handler(
        client: McpStdioClient, tool: str
    ) -> Callable[[dict[str, Any]], Awaitable[str]]:
        async def handler(args: dict[str, Any]) -> str:
            text, is_error = await client.call_tool(tool, args)
            return f"Error: {text}" if is_error else text

        return handler

    # ---- the run -------------------------------------------------------------------------
    async def run(self, agent: AgentDefinition, task: str) -> RunResult:
        system = self._system_prompt(agent)
        context: list[str] = []
        context_ids: list[str] = []
        if "knowledge.search" in agent.tools:
            for hit in await self._knowledge.search(task[:300], limit=3):
                context.append(f"[{hit.id}] {hit.title}: {hit.snippet}")
                context_ids.append(hit.id)
        prompt = f"<input>\n{task}\n</input>"
        if context:
            prompt += "\n<knowledge>\n" + "\n".join(context) + "\n</knowledge>"

        clients: list[McpStdioClient] = []
        try:
            toolbox = {**self._static_tools(agent), **await self._start_mcp(agent, clients)}
            return await self._converse(agent, system, prompt, toolbox, context_ids)
        finally:
            for client in clients:
                await client.close()

    async def _converse(
        self,
        agent: AgentDefinition,
        system: str,
        prompt: str,
        toolbox: dict[str, RuntimeTool],
        context_ids: list[str],
    ) -> RunResult:
        provider = self._provider()
        model = agent.model or self._model
        specs = [t.spec for t in toolbox.values()]
        messages = [ChatMessage(role="user", content=prompt)]
        used: list[str] = []
        tokens_in: int | None = None
        tokens_out: int | None = None
        for step in range(1, self._max_steps + 1):
            turn = await provider.chat(
                messages, tools=specs, model=model, system=system, max_tokens=self._max_tokens
            )
            if turn.input_tokens is not None:
                tokens_in = (tokens_in or 0) + turn.input_tokens
            if turn.output_tokens is not None:
                tokens_out = (tokens_out or 0) + turn.output_tokens
            if not turn.tool_calls:
                return RunResult(
                    text=turn.text, model=turn.model, steps=step, input_tokens=tokens_in,
                    output_tokens=tokens_out, context_documents=context_ids, tools_used=used,
                )  # fmt: skip
            messages.append(
                ChatMessage(role="assistant", content=turn.text, tool_calls=turn.tool_calls)
            )
            for call in turn.tool_calls:
                result = await self._call_tool(agent, toolbox, call.name, call.arguments)
                used.append(call.name)
                messages.append(ChatMessage(role="tool", content=result, tool_call_id=call.id))
        raise APIError(
            f"The agent did not finish within {self._max_steps} steps.",
            hint="Raise `agents.max_steps` or narrow the task.",
        )

    async def _call_tool(
        self,
        agent: AgentDefinition,
        toolbox: dict[str, RuntimeTool],
        name: str,
        args: dict[str, Any],
    ) -> str:
        tool = toolbox.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'."
        if tool.permission not in agent.permissions:  # least privilege, checked on every call
            return f"Error: this agent is not permitted to use '{name}'."
        log.info("agent %s calls %s(%s)", agent.name, name, json.dumps(args)[:200])
        try:
            output = await asyncio.wait_for(tool.handler(args), self._tool_timeout)
        except StratosError as exc:
            return f"Error: {exc.message}"
        except TimeoutError:
            return "Error: the tool timed out."
        return sanitize_text(self._redactor.redact_text(output))[:MAX_TOOL_OUTPUT]
