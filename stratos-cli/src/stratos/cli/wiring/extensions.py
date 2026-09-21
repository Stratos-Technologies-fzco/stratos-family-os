"""Claude Code, skills, MCP, AI, knowledge and agents (M13-M18)."""

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from platformdirs import user_cache_dir

from stratos.domain.enums import AgentPermission
from stratos.domain.exceptions import ConfigurationError
from stratos.domain.interfaces import AIProvider, ClaudeProject, KnowledgeProvider

if TYPE_CHECKING:
    from stratos.application.agent_service import AgentService
    from stratos.application.ai_service import AIService
    from stratos.application.claude_service import ClaudeIntegrationService
    from stratos.application.knowledge_service import KnowledgeService
    from stratos.application.mcp_service import McpService
    from stratos.application.skill_service import SkillService
    from stratos.cli.context import CliContext


def claude_manager(ctx: "CliContext", project_dir: Path | None = None) -> ClaudeProject:
    from stratos.infrastructure.claude.manager import ClaudeCodeManager

    return ClaudeCodeManager(project_dir or ctx.project_dir, ctx.protector)


def claude_service(ctx: "CliContext", *, dry_run: bool) -> "ClaudeIntegrationService":
    from stratos.application.claude_service import ClaudeIntegrationService
    from stratos.infrastructure.filesystem.agent_registry import FilesystemAgentRegistry

    return ClaudeIntegrationService(
        ctx.claude,
        FilesystemAgentRegistry(ctx.project_dir),
        ctx.require,
        ctx.audit,
        ctx.guard(dry_run=dry_run),
    )


def registry_root(configured: str | None, key: str) -> Path:
    if not configured:
        raise ConfigurationError(
            f"No registry is configured for {key}.",
            hint=f"Run `stratos config set {key} <directory>`.",
        )
    return Path(configured).expanduser()


def skill_service(
    ctx: "CliContext", *, dry_run: bool, yes: bool, project_dir: Path | None
) -> "SkillService":
    from stratos import __version__
    from stratos.application.skill_service import SkillService
    from stratos.infrastructure.filesystem.skill_registry import LocalSkillRegistry

    root = registry_root(ctx.settings.skills.registry, "skills.registry")
    claude = ctx.claude if project_dir is None else claude_manager(ctx, project_dir)
    return SkillService(
        LocalSkillRegistry(root),
        claude,
        ctx.protector,
        ctx.require,
        ctx.audit,
        ctx.guard(dry_run=dry_run, yes=yes),
        ctx.cache,
        stratos_version=__version__,
        registry_id=str(root.resolve()),
    )


def mcp_service(
    ctx: "CliContext", *, dry_run: bool, yes: bool, project_dir: Path | None
) -> "McpService":
    from stratos.application.mcp_service import McpService
    from stratos.infrastructure.mcp.registry import LocalMcpRegistry

    root = registry_root(ctx.settings.mcp.registry, "mcp.registry")
    claude = ctx.claude if project_dir is None else claude_manager(ctx, project_dir)
    return McpService(
        LocalMcpRegistry(root),
        claude,
        ctx.require,
        ctx.audit,
        ctx.guard(dry_run=dry_run, yes=yes),
        ctx.cache,
        allowed=ctx.settings.mcp.allowed,
        environ=os.environ,
        registry_id=str(root.resolve()),
    )


# ---- AI ---------------------------------------------------------------------------------
def ai_provider(ctx: "CliContext") -> AIProvider:
    from stratos.infrastructure.ai import api_key_env_for, create_provider

    ai = ctx.settings.ai
    return create_provider(
        ai.provider,
        api_key=os.environ.get(api_key_env_for(ai.provider)),
        default_model=ai.model,
        azure_endpoint=ai.azure_endpoint,
        azure_api_version=ai.azure_api_version,
    )


def ai_service(ctx: "CliContext") -> "AIService":
    from stratos.application.ai_service import AIService
    from stratos.infrastructure.ai import api_key_env_for

    ai = ctx.settings.ai
    return AIService(
        lambda: ctx.ai_provider,
        ctx.require,
        provider_name=ai.provider,
        default_model=ai.model,
        max_tokens=ai.max_tokens,
        api_key_configured=bool(os.environ.get(api_key_env_for(ai.provider))),
        check_provider=ctx.policy.check_provider,
        check_model=ctx.policy.check_model,
    )


# ---- knowledge --------------------------------------------------------------------------
def knowledge_source_labels(ctx: "CliContext") -> list[str]:
    k = ctx.settings.knowledge
    labels = [f"Markdown folder: {p}" for p in k.paths]
    labels += [f"PDF folder: {p}" for p in k.pdf_paths]
    labels += [f"GitHub: {g}" for g in k.github]
    if k.confluence_url:
        labels.append(f"Confluence: {k.confluence_url}")
    labels += [f"SharePoint: {s}" for s in k.sharepoint_sites]
    return labels


def _local_providers(ctx: "CliContext", cache_dir: Path) -> list[KnowledgeProvider]:
    from stratos.infrastructure.knowledge.markdown import MarkdownKnowledgeProvider
    from stratos.infrastructure.knowledge.pdf import PdfKnowledgeProvider

    k = ctx.settings.knowledge
    providers: list[KnowledgeProvider] = []
    if k.paths:
        roots = [Path(p).expanduser() for p in k.paths]
        manifest = cache_dir / "knowledge-manifest.json"
        providers.append(MarkdownKnowledgeProvider(roots, manifest, redactor=ctx.redactor))
    if k.pdf_paths:
        roots = [Path(p).expanduser() for p in k.pdf_paths]
        manifest = cache_dir / "knowledge-pdf-manifest.json"
        providers.append(PdfKnowledgeProvider(roots, manifest, redactor=ctx.redactor))
    return providers


def _remote_providers(ctx: "CliContext", cache_dir: Path) -> list[KnowledgeProvider]:
    from stratos.infrastructure.api.async_client import AsyncPlatformApiClient
    from stratos.infrastructure.github.service import GITHUB_HEADERS
    from stratos.infrastructure.github.token import GithubTokenProvider
    from stratos.infrastructure.knowledge import remote

    k = ctx.settings.knowledge
    providers: list[KnowledgeProvider] = []
    if k.github:
        client = AsyncPlatformApiClient(
            ctx.settings.github.api_url,
            GithubTokenProvider(),
            default_headers=GITHUB_HEADERS,
            send_correlation=False,
        )
        manifest = cache_dir / "knowledge-github-manifest.json"
        providers.append(
            remote.GitHubKnowledgeProvider(client, k.github, manifest, redactor=ctx.redactor)
        )
    if k.confluence_url:
        client = AsyncPlatformApiClient(
            k.confluence_url,
            remote.basic_auth_from_env(os.environ),
            auth_scheme="Basic",
            send_correlation=False,
        )
        providers.append(
            remote.ConfluenceKnowledgeProvider(
                client, spaces=k.confluence_spaces, redactor=ctx.redactor
            )
        )
    if k.sharepoint_sites:
        client = AsyncPlatformApiClient(
            "https://graph.microsoft.com",
            remote.env_token(os.environ, "SHAREPOINT_TOKEN", "MS_GRAPH_TOKEN"),
            send_correlation=False,
        )
        providers.append(
            remote.SharePointKnowledgeProvider(client, k.sharepoint_sites, redactor=ctx.redactor)
        )
    return providers


def knowledge_providers(ctx: "CliContext") -> list[KnowledgeProvider]:
    """One provider per configured source; nothing is created for unconfigured ones."""
    cache_dir = Path(user_cache_dir("stratos", appauthor=False))
    providers = _local_providers(ctx, cache_dir) + _remote_providers(ctx, cache_dir)
    if not providers:
        raise ConfigurationError(
            "No knowledge sources are configured.",
            hint=(
                "Set one of: knowledge.paths, knowledge.pdf_paths, knowledge.github, "
                "knowledge.confluence_url, knowledge.sharepoint_sites."
            ),
        )
    return providers


def knowledge_service(ctx: "CliContext") -> "KnowledgeService":
    from stratos.application.knowledge_service import KnowledgeService

    return KnowledgeService(ctx.knowledge_providers, ctx.require)


# ---- agents -----------------------------------------------------------------------------
def load_skill(ctx: "CliContext", name: str) -> str | None:
    """Text of an installed skill's SKILL.md, or None."""
    from stratos.utils.validation import validate_slug

    try:
        validate_slug(name, "skill name")
    except Exception:  # noqa: BLE001 - invalid names simply are not installed
        return None
    path = ctx.claude.skills_dir / name / "SKILL.md"
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None


def _mcp_factory(name: str, config: dict[str, Any]) -> Any:
    from stratos.infrastructure.mcp.client import McpStdioClient, build_server_env

    env = build_server_env(config.get("env") or {}, os.environ)
    return McpStdioClient(config["command"], list(config.get("args", [])), env)


def agent_service(ctx: "CliContext", *, dry_run: bool, yes: bool) -> "AgentService":
    from stratos.application.agent_runner import AgentRunner
    from stratos.application.agent_service import AgentService
    from stratos.infrastructure.filesystem.agent_registry import (
        FilesystemAgentRegistry,
        FilesystemAgentRunLog,
    )

    s = ctx.settings
    try:
        allowed = {AgentPermission(p) for p in s.agents.allowed_permissions}
    except ValueError as exc:
        raise ConfigurationError("agents.allowed_permissions contains an unknown value.") from exc
    mcp_allowed = s.mcp.allowed
    runner = AgentRunner(
        lambda: ctx.ai_provider,
        ctx.knowledge_service,
        ctx.project_dir,
        ctx.redactor,
        default_model=s.ai.model,
        max_tokens=s.ai.max_tokens,
        max_steps=s.agents.max_steps,
        mcp_configs=ctx.claude.mcp_servers,
        mcp_allowed=lambda name: mcp_allowed is None or name in mcp_allowed,
        mcp_factory=_mcp_factory,
        skill_loader=lambda name: load_skill(ctx, name),
    )
    return AgentService(
        FilesystemAgentRegistry(ctx.project_dir),
        FilesystemAgentRunLog(ctx.project_dir, ctx.redactor),
        runner,
        ctx.require,
        ctx.audit,
        ctx.guard(dry_run=dry_run, yes=yes),
        allowed_permissions=allowed,
        check_model=ctx.policy.check_model,
        default_model=s.ai.model,
    )
