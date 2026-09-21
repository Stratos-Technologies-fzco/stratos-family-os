"""Per-invocation command context (dependency injection).

Everything expensive is built lazily, so `--help` never touches configuration, the keyring,
the network or any client, and unrelated commands never load AI, GitHub or knowledge clients.
"""

import os
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stratos.cli.output import ConsoleRenderer
from stratos.config.loader import load_settings
from stratos.config.settings import Settings
from stratos.domain.enums import Permission
from stratos.domain.exceptions import AuthenticationError, ConfigurationError
from stratos.domain.models.auth import Identity
from stratos.utils.redaction import SecretRedactor

if TYPE_CHECKING:
    from stratos.application.agent_service import AgentService
    from stratos.application.ai_service import AIService
    from stratos.application.audit_service import AuditService
    from stratos.application.auth_service import AuthService
    from stratos.application.authorization import AuthorizationService
    from stratos.application.backup_service import BackupService
    from stratos.application.claude_service import ClaudeIntegrationService
    from stratos.application.deployment_service import DeploymentService, EnvironmentService
    from stratos.application.diagnostics import Facts
    from stratos.application.init_service import InitService
    from stratos.application.knowledge_service import KnowledgeService
    from stratos.application.mcp_service import McpService
    from stratos.application.org_service import OrgService, TeamService
    from stratos.application.policy_service import PolicyService
    from stratos.application.project_service import ProjectService
    from stratos.application.repository_service import RepositoryService
    from stratos.application.safety import OperationGuard
    from stratos.application.skill_service import SkillService
    from stratos.application.workspace_service import WorkspaceService
    from stratos.domain.interfaces import AIProvider, ClaudeProject, KnowledgeProvider
    from stratos.infrastructure.api.client import PlatformApiClient
    from stratos.infrastructure.filesystem.cache import TtlCache
    from stratos.infrastructure.filesystem.config_files import ConfigFileProtector
    from stratos.infrastructure.filesystem.registry_store import (
        LocalProjectStore,
        LocalWorkspaceStore,
    )
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

        if os.environ.get("STRATOS_DEV_AUTH_BYPASS") == "1":
            from stratos.application.auth_service import DevAuthService

            return DevAuthService()  # type: ignore[return-value]  # duck-typed local stand-in

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

    def backup_service(self, *, dry_run: bool = False, yes: bool = False) -> "BackupService":
        from stratos.application.backup_service import BackupService
        from stratos.infrastructure.filesystem.backups import LocalBackupCatalog

        return BackupService(
            LocalBackupCatalog(self.project_dir),
            self.protector,
            self.require,
            self.audit,
            self.guard(dry_run=dry_run, yes=yes),
        )

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
        from stratos.infrastructure.github.git import clone_repository

        return RepositoryService(
            self.github,
            self.require,
            self.audit,
            self.guard(dry_run=dry_run, yes=yes),
            self.default_org,
            clone=clone_repository,
            policy=self.policy,
        )

    @cached_property
    def org_service(self) -> "OrgService":
        from stratos.application.org_service import OrgService

        return OrgService(
            self.github, self.require, self.cache, self.default_org, self.policy.summary
        )

    @cached_property
    def team_service(self) -> "TeamService":
        from stratos.application.org_service import TeamService

        return TeamService(self.github, self.require, self.cache, self.default_org)

    # ---- Claude Code, skills, MCP (M13-M15) ----------------------------------------------
    @cached_property
    def claude(self) -> "ClaudeProject":
        from stratos.cli.wiring.extensions import claude_manager

        return claude_manager(self)

    def claude_service(self, *, dry_run: bool = False) -> "ClaudeIntegrationService":
        from stratos.cli.wiring.extensions import claude_service

        return claude_service(self, dry_run=dry_run)

    def skill_service(
        self, *, dry_run: bool = False, yes: bool = False, project_dir: Path | None = None
    ) -> "SkillService":
        from stratos.cli.wiring.extensions import skill_service

        return skill_service(self, dry_run=dry_run, yes=yes, project_dir=project_dir)

    def mcp_service(
        self, *, dry_run: bool = False, yes: bool = False, project_dir: Path | None = None
    ) -> "McpService":
        from stratos.cli.wiring.extensions import mcp_service

        return mcp_service(self, dry_run=dry_run, yes=yes, project_dir=project_dir)

    # ---- AI, knowledge, agents (M16-M18) -------------------------------------------------
    @cached_property
    def ai_provider(self) -> "AIProvider":
        from stratos.cli.wiring.extensions import ai_provider

        return ai_provider(self)

    @cached_property
    def ai_service(self) -> "AIService":
        from stratos.cli.wiring.extensions import ai_service

        return ai_service(self)

    def knowledge_source_labels(self) -> list[str]:
        from stratos.cli.wiring.extensions import knowledge_source_labels

        return knowledge_source_labels(self)

    def knowledge_providers(self) -> "list[KnowledgeProvider]":
        from stratos.cli.wiring.extensions import knowledge_providers

        return knowledge_providers(self)

    @cached_property
    def knowledge_service(self) -> "KnowledgeService":
        from stratos.cli.wiring.extensions import knowledge_service

        return knowledge_service(self)

    def agent_service(self, *, dry_run: bool = False, yes: bool = False) -> "AgentService":
        from stratos.cli.wiring.extensions import agent_service

        return agent_service(self, dry_run=dry_run, yes=yes)

    # ---- Layer D: projects, init, workspaces, environments, deployments ------------------
    @cached_property
    def project_store(self) -> "LocalProjectStore":
        from stratos.infrastructure.filesystem.registry_store import LocalProjectStore

        return LocalProjectStore()

    @cached_property
    def workspace_store(self) -> "LocalWorkspaceStore":
        from stratos.infrastructure.filesystem.registry_store import LocalWorkspaceStore

        return LocalWorkspaceStore()

    def project_service(self, *, dry_run: bool = False, yes: bool = False) -> "ProjectService":
        from stratos.cli.wiring.workflow import project_service

        return project_service(self, dry_run=dry_run, yes=yes)

    def init_service(
        self, project_dir: Path | None = None, *, dry_run: bool = False, yes: bool = False
    ) -> "InitService":
        from stratos.cli.wiring.workflow import init_service

        return init_service(self, project_dir, dry_run=dry_run, yes=yes)

    def workspace_service(self, *, dry_run: bool = False, yes: bool = False) -> "WorkspaceService":
        from stratos.cli.wiring.workflow import workspace_service

        return workspace_service(self, dry_run=dry_run, yes=yes)

    def environment_service(
        self, *, dry_run: bool = False, yes: bool = False
    ) -> "EnvironmentService":
        from stratos.cli.wiring.workflow import environment_service

        return environment_service(self, dry_run=dry_run, yes=yes)

    def deployment_service(
        self, *, dry_run: bool = False, yes: bool = False
    ) -> "DeploymentService":
        from stratos.cli.wiring.workflow import deployment_service

        return deployment_service(self, dry_run=dry_run, yes=yes)

    def resolve_project(
        self, project: str | None, org: str | None = None
    ) -> tuple[str, str | None]:
        from stratos.cli.wiring.workflow import resolve_project

        return resolve_project(self, project, org)

    # ---- diagnostics (M23) ---------------------------------------------------------------
    def diagnostics_facts(self, *, online: bool = False) -> "Facts":
        """Gather facts for `stratos doctor`. Network probes only when `online` is set."""
        from stratos.cli.wiring.diagnostics import gather_facts

        return gather_facts(self, online=online)
