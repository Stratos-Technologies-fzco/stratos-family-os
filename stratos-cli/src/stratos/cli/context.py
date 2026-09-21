"""Per-invocation command context (dependency injection).

Everything expensive is built lazily, so `--help` never touches configuration, the keyring,
the network or any client, and unrelated commands never load AI, GitHub or knowledge clients.
"""

import os
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from platformdirs import user_cache_dir

from stratos.cli.output import ConsoleRenderer
from stratos.config.loader import load_settings
from stratos.config.settings import Settings
from stratos.domain.enums import AgentPermission, Permission
from stratos.domain.exceptions import AuthenticationError, ConfigurationError
from stratos.domain.models.auth import Identity
from stratos.utils.redaction import SecretRedactor

if TYPE_CHECKING:
    from stratos.application.agent_service import AgentService
    from stratos.application.ai_service import AIService
    from stratos.application.audit_service import AuditService
    from stratos.application.auth_service import AuthService
    from stratos.application.authorization import AuthorizationService
    from stratos.application.claude_service import ClaudeIntegrationService
    from stratos.application.diagnostics import Facts
    from stratos.application.knowledge_service import KnowledgeService
    from stratos.application.mcp_service import McpService
    from stratos.application.org_service import OrgService, TeamService
    from stratos.application.policy_service import PolicyService
    from stratos.application.repository_service import RepositoryService
    from stratos.application.safety import OperationGuard
    from stratos.application.skill_service import SkillService
    from stratos.domain.interfaces import AIProvider, KnowledgeProvider
    from stratos.infrastructure.api.client import PlatformApiClient
    from stratos.infrastructure.claude.manager import ClaudeCodeManager
    from stratos.infrastructure.filesystem.cache import TtlCache
    from stratos.infrastructure.filesystem.config_files import ConfigFileProtector
    from stratos.infrastructure.github.service import GitHubService


@dataclass
class CliContext:
    renderer: ConsoleRenderer
    redactor: SecretRedactor = field(default_factory=SecretRedactor)
    verbose: bool = False
    debug: bool = False
    overrides: dict[str, Any] = field(default_factory=dict)

    # ---- configuration and identity ------------------------------------------------------
    @cached_property
    def settings(self) -> Settings:
        return load_settings(self.overrides)

    @cached_property
    def project_dir(self) -> Path:
        return Path.cwd()

    @cached_property
    def auth_service(self) -> "AuthService":
        from stratos.application.auth_service import AuthService
        from stratos.infrastructure.auth.loopback import LoopbackReceiver
        from stratos.infrastructure.auth.oidc import OidcProvider
        from stratos.infrastructure.secrets import KeyringSecretStore

        auth = self.settings.auth
        if not auth.issuer or not auth.client_id:
            raise ConfigurationError(
                "Sign-in is not configured.",
                hint=(
                    "Run `stratos config set auth.issuer <url>` and "
                    "`stratos config set auth.client_id <id>`."
                ),
            )
        return AuthService(
            OidcProvider(auth.issuer, auth.client_id, auth.scopes),
            KeyringSecretStore(),
            issuer=auth.issuer,
            receiver_factory=LoopbackReceiver,
        )

    @cached_property
    def authorization(self) -> "AuthorizationService":
        from stratos.application.authorization import AuthorizationService

        return AuthorizationService()

    def _identity_or_none(self) -> Identity | None:
        try:
            return self.auth_service.whoami()
        except (AuthenticationError, ConfigurationError):
            return None

    def require(self, permission: Permission) -> Identity:
        """Return the signed-in identity after checking it holds `permission`."""
        identity = self.auth_service.whoami()
        self.authorization.require(identity, permission)
        return identity

    # ---- shared infrastructure -----------------------------------------------------------
    @cached_property
    def api(self) -> "PlatformApiClient":
        from stratos.infrastructure.api.client import PlatformApiClient

        return PlatformApiClient(str(self.settings.api.endpoint), self.auth_service.access_token)

    @cached_property
    def audit(self) -> "AuditService":
        from stratos.application.audit_service import AuditService
        from stratos.infrastructure.filesystem.audit_log import LocalAuditStore

        return AuditService(LocalAuditStore(redactor=self.redactor), self._identity_or_none)

    @cached_property
    def cache(self) -> "TtlCache":
        from stratos.infrastructure.filesystem.cache import TtlCache

        return TtlCache(redactor=self.redactor)

    @cached_property
    def protector(self) -> "ConfigFileProtector":
        from stratos.infrastructure.filesystem.config_files import ConfigFileProtector

        return ConfigFileProtector(self.redactor)

    def guard(self, *, dry_run: bool = False, yes: bool = False) -> "OperationGuard":
        from stratos.application.safety import OperationGuard, SafetyOptions

        return OperationGuard(self.renderer, SafetyOptions(dry_run=dry_run, assume_yes=yes))

    @property
    def default_org(self) -> str:
        return self.settings.github.organization

    # ---- GitHub (M11, M12) ---------------------------------------------------------------
    @cached_property
    def github(self) -> "GitHubService":
        from stratos.infrastructure.api.client import PlatformApiClient
        from stratos.infrastructure.github.service import GITHUB_HEADERS, GitHubService
        from stratos.infrastructure.github.token import GithubTokenProvider

        client = PlatformApiClient(
            self.settings.github.api_url,
            GithubTokenProvider(),
            default_headers=GITHUB_HEADERS,
            send_correlation=False,
        )
        return GitHubService(client)

    @cached_property
    def policy(self) -> "PolicyService":
        from stratos.application.policy_service import PolicyService

        return PolicyService(self.settings)

    def repository_service(
        self, *, dry_run: bool = False, yes: bool = False
    ) -> "RepositoryService":
        from stratos.application.repository_service import RepositoryService

        return RepositoryService(
            self.github,
            self.require,
            self.audit,
            self.guard(dry_run=dry_run, yes=yes),
            self.default_org,
            policy=self.policy,
        )

    @cached_property
    def org_service(self) -> "OrgService":
        from stratos.application.org_service import OrgService

        return OrgService(
            self.github,
            self.require,
            self.cache,
            self.default_org,
            self.policy.summary,
        )

    @cached_property
    def team_service(self) -> "TeamService":
        from stratos.application.org_service import TeamService

        return TeamService(self.github, self.require, self.cache, self.default_org)

    # ---- Claude Code, skills, MCP (M13-M15) ----------------------------------------------
    @cached_property
    def claude(self) -> "ClaudeCodeManager":
        from stratos.infrastructure.claude.manager import ClaudeCodeManager

        return ClaudeCodeManager(self.project_dir, self.protector)

    def claude_service(self, *, dry_run: bool = False) -> "ClaudeIntegrationService":
        from stratos.application.claude_service import ClaudeIntegrationService
        from stratos.infrastructure.filesystem.agent_registry import FilesystemAgentRegistry

        return ClaudeIntegrationService(
            self.claude,
            FilesystemAgentRegistry(self.project_dir),
            self.require,
            self.audit,
            self.guard(dry_run=dry_run),
        )

    def knowledge_source_labels(self) -> list[str]:
        k = self.settings.knowledge
        labels = [f"Markdown folder: {p}" for p in k.paths]
        labels += [f"PDF folder: {p}" for p in k.pdf_paths]
        labels += [f"GitHub: {g}" for g in k.github]
        if k.confluence_url:
            labels.append(f"Confluence: {k.confluence_url}")
        labels += [f"SharePoint: {s}" for s in k.sharepoint_sites]
        return labels

    def _registry_root(self, configured: str | None, key: str) -> Path:
        if not configured:
            raise ConfigurationError(
                f"No registry is configured for {key}.",
                hint=f"Run `stratos config set {key} <directory>`.",
            )
        return Path(configured).expanduser()

    def skill_service(self, *, dry_run: bool = False, yes: bool = False) -> "SkillService":
        from stratos import __version__
        from stratos.application.skill_service import SkillService
        from stratos.infrastructure.filesystem.skill_registry import LocalSkillRegistry

        root = self._registry_root(self.settings.skills.registry, "skills.registry")
        return SkillService(
            LocalSkillRegistry(root),
            self.claude,
            self.protector,
            self.require,
            self.audit,
            self.guard(dry_run=dry_run, yes=yes),
            self.cache,
            stratos_version=__version__,
            registry_id=str(root.resolve()),
        )

    def mcp_service(self, *, dry_run: bool = False, yes: bool = False) -> "McpService":
        from stratos.application.mcp_service import McpService
        from stratos.infrastructure.mcp.registry import LocalMcpRegistry

        root = self._registry_root(self.settings.mcp.registry, "mcp.registry")
        return McpService(
            LocalMcpRegistry(root),
            self.claude,
            self.require,
            self.audit,
            self.guard(dry_run=dry_run, yes=yes),
            self.cache,
            allowed=self.settings.mcp.allowed,
            environ=os.environ,
            registry_id=str(root.resolve()),
        )

    # ---- AI, knowledge, agents (M16-M18) -------------------------------------------------
    @cached_property
    def ai_provider(self) -> "AIProvider":
        from stratos.infrastructure.ai import api_key_env_for, create_provider

        ai = self.settings.ai
        return create_provider(
            ai.provider,
            api_key=os.environ.get(api_key_env_for(ai.provider)),
            default_model=ai.model,
            azure_endpoint=ai.azure_endpoint,
            azure_api_version=ai.azure_api_version,
        )

    @cached_property
    def ai_service(self) -> "AIService":
        from stratos.application.ai_service import AIService
        from stratos.infrastructure.ai import api_key_env_for

        ai = self.settings.ai
        return AIService(
            lambda: self.ai_provider,
            self.require,
            provider_name=ai.provider,
            default_model=ai.model,
            max_tokens=ai.max_tokens,
            api_key_configured=bool(os.environ.get(api_key_env_for(ai.provider))),
            check_provider=self.policy.check_provider,
            check_model=self.policy.check_model,
        )

    def _knowledge_providers(self) -> "list[KnowledgeProvider]":
        """One provider per configured source; nothing is created for unconfigured ones."""
        from stratos.infrastructure.api.async_client import AsyncPlatformApiClient
        from stratos.infrastructure.github.service import GITHUB_HEADERS
        from stratos.infrastructure.github.token import GithubTokenProvider
        from stratos.infrastructure.knowledge import remote
        from stratos.infrastructure.knowledge.markdown import MarkdownKnowledgeProvider
        from stratos.infrastructure.knowledge.pdf import PdfKnowledgeProvider

        k = self.settings.knowledge
        cache_dir = Path(user_cache_dir("stratos", appauthor=False))
        providers: list[KnowledgeProvider] = []
        if k.paths:
            manifest = cache_dir / "knowledge-manifest.json"
            roots = [Path(p).expanduser() for p in k.paths]
            providers.append(MarkdownKnowledgeProvider(roots, manifest, redactor=self.redactor))
        if k.pdf_paths:
            manifest = cache_dir / "knowledge-pdf-manifest.json"
            roots = [Path(p).expanduser() for p in k.pdf_paths]
            providers.append(PdfKnowledgeProvider(roots, manifest, redactor=self.redactor))
        if k.github:
            client = AsyncPlatformApiClient(
                self.settings.github.api_url,
                GithubTokenProvider(),
                default_headers=GITHUB_HEADERS,
                send_correlation=False,
            )
            manifest = cache_dir / "knowledge-github-manifest.json"
            providers.append(
                remote.GitHubKnowledgeProvider(client, k.github, manifest, redactor=self.redactor)
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
                    client, spaces=k.confluence_spaces, redactor=self.redactor
                )
            )
        if k.sharepoint_sites:
            client = AsyncPlatformApiClient(
                "https://graph.microsoft.com",
                remote.env_token(os.environ, "SHAREPOINT_TOKEN", "MS_GRAPH_TOKEN"),
                send_correlation=False,
            )
            providers.append(
                remote.SharePointKnowledgeProvider(
                    client, k.sharepoint_sites, redactor=self.redactor
                )
            )
        if not providers:
            raise ConfigurationError(
                "No knowledge sources are configured.",
                hint=(
                    "Set one of: knowledge.paths, knowledge.pdf_paths, knowledge.github, "
                    "knowledge.confluence_url, knowledge.sharepoint_sites."
                ),
            )
        return providers

    @cached_property
    def knowledge_service(self) -> "KnowledgeService":
        from stratos.application.knowledge_service import KnowledgeService

        return KnowledgeService(self._knowledge_providers, self.require)

    def _load_skill(self, name: str) -> str | None:
        """Text of an installed skill's SKILL.md, or None."""
        from stratos.utils.validation import validate_slug

        try:
            validate_slug(name, "skill name")
        except Exception:  # noqa: BLE001 - invalid names simply are not installed
            return None
        path = self.claude.skills_dir / name / "SKILL.md"
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None

    def agent_service(self, *, dry_run: bool = False, yes: bool = False) -> "AgentService":
        from stratos.application.agent_runner import AgentRunner
        from stratos.application.agent_service import AgentService
        from stratos.infrastructure.filesystem.agent_registry import (
            FilesystemAgentRegistry,
            FilesystemAgentRunLog,
        )

        s = self.settings
        try:
            allowed = {AgentPermission(p) for p in s.agents.allowed_permissions}
        except ValueError as exc:
            raise ConfigurationError(
                "agents.allowed_permissions contains an unknown value."
            ) from exc
        mcp_allowed = s.mcp.allowed
        runner = AgentRunner(
            lambda: self.ai_provider,
            self.knowledge_service,
            self.project_dir,
            self.redactor,
            default_model=s.ai.model,
            max_tokens=s.ai.max_tokens,
            max_steps=s.agents.max_steps,
            mcp_configs=self.claude.mcp_servers,
            mcp_allowed=lambda name: mcp_allowed is None or name in mcp_allowed,
            environ=os.environ,
            skill_loader=self._load_skill,
        )
        return AgentService(
            FilesystemAgentRegistry(self.project_dir),
            FilesystemAgentRunLog(self.project_dir, self.redactor),
            runner,
            self.require,
            self.audit,
            self.guard(dry_run=dry_run, yes=yes),
            allowed_permissions=allowed,
            check_model=self.policy.check_model,
            default_model=s.ai.model,
        )

    # ---- diagnostics (M23) ---------------------------------------------------------------
    def diagnostics_facts(self) -> "Facts":
        """Gather local facts for `stratos doctor`. Never makes a network call."""
        import shutil
        import sys

        from stratos.application.diagnostics import Facts
        from stratos.infrastructure.ai import api_key_env_for

        config_error: str | None = None
        try:
            settings = self.settings
        except ConfigurationError as exc:
            config_error = exc.message
            settings = load_settings(
                {},
                env={},
                org_path=Path(os.devnull),
                user_path=Path(os.devnull),
                project_path=Path(os.devnull),
            )  # defaults, for the other checks
        keyring_backend: str | None
        try:
            import keyring

            backend = keyring.get_keyring()
            keyring_backend = (
                f"{type(backend).__module__.split('.')[-2:][0]}.{type(backend).__name__}"
            )
        except Exception:  # noqa: BLE001 - any failure means "not usable"
            keyring_backend = None
        auth_ok = bool(settings.auth.issuer and settings.auth.client_id)
        signed_in: bool | None = None
        if auth_ok:
            try:
                signed_in = self.auth_service.status().authenticated
            except Exception:  # noqa: BLE001
                signed_in = False
        gh_token = bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
        if not gh_token and shutil.which("gh"):
            import subprocess

            try:
                out = subprocess.run(
                    ["gh", "auth", "status"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                gh_token = out.returncode == 0
            except (OSError, subprocess.SubprocessError):
                gh_token = False
        level, detail = self.claude.detect().doctor_check()
        k = settings.knowledge
        sources = len(k.paths) + len(k.pdf_paths) + len(k.github) + len(k.sharepoint_sites)
        sources += 1 if k.confluence_url else 0
        return Facts(
            python=(sys.version_info.major, sys.version_info.minor),
            config_error=config_error,
            keyring_backend=keyring_backend,
            auth_configured=auth_ok,
            signed_in=signed_in,
            git_found=shutil.which("git") is not None,
            github_token_found=gh_token,
            claude_level=level,
            claude_detail=detail,
            ai_provider=settings.ai.provider,
            ai_key_configured=bool(os.environ.get(api_key_env_for(settings.ai.provider))),
            skills_registry=bool(settings.skills.registry),
            mcp_registry=bool(settings.mcp.registry),
            knowledge_sources=sources,
        )
