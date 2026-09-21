"""Enumerations: output formats, exit codes, permissions, roles, audit actions."""

from enum import IntEnum, StrEnum


class OutputFormat(StrEnum):
    TABLE = "table"
    JSON = "json"
    YAML = "yaml"
    PLAIN = "plain"
    QUIET = "quiet"


class LogLevel(StrEnum):
    WARNING = "WARNING"
    INFO = "INFO"
    DEBUG = "DEBUG"


class ExitCode(IntEnum):
    """Stable exit codes (module structure, Appendix A)."""

    SUCCESS = 0
    GENERAL_ERROR = 1
    INVALID_USAGE = 2
    AUTHENTICATION = 3
    AUTHORIZATION = 4
    NOT_FOUND = 5
    VALIDATION = 6
    NETWORK = 7
    CONFIGURATION = 8
    DEPENDENCY = 9
    CANCELLED = 10


class AuditAction(StrEnum):
    """Sensitive actions that must produce an audit event."""

    PROJECT_CREATE = "project.create"
    PROJECT_DELETE = "project.delete"
    REPO_CREATE = "repo.create"
    REPO_DELETE = "repo.delete"
    AGENT_RUN = "agent.run"
    DEPLOYMENT_CREATE = "deployment.create"
    DEPLOYMENT_ROLLBACK = "deployment.rollback"
    MCP_INSTALL = "mcp.install"
    SKILL_INSTALL = "skill.install"
    REPO_CONFIGURE = "repo.configure"  # extension beyond the spec list
    REPO_ARCHIVE = "repo.archive"  # extension beyond the spec list
    SKILL_REMOVE = "skill.remove"  # extension beyond the spec list
    MCP_CONFIGURE = "mcp.configure"  # extension beyond the spec list
    AGENT_CREATE = "agent.create"  # extension beyond the spec list
    CLAUDE_CONFIGURE = "claude.configure"  # extension beyond the spec list
    BACKUP_RESTORE = "backup.restore"  # extension beyond the spec list
    PROJECT_UPDATE = "project.update"  # extension beyond the spec list
    PROJECT_INIT = "project.init"  # extension beyond the spec list
    WORKSPACE_CREATE = "workspace.create"  # extension beyond the spec list
    WORKSPACE_DELETE = "workspace.delete"  # extension beyond the spec list
    ENVIRONMENT_CREATE = "environment.create"  # extension beyond the spec list
    ENVIRONMENT_DELETE = "environment.delete"  # extension beyond the spec list


class AuditResult(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class Role(StrEnum):
    VIEWER = "viewer"
    DEVELOPER = "developer"
    MAINTAINER = "maintainer"
    ADMIN = "admin"


class Permission(StrEnum):
    """Permissions checked before sensitive operations. Names mirror audit actions."""

    ORG_READ = "org.read"
    AUDIT_READ = "audit.read"
    PROJECT_CREATE = "project.create"
    PROJECT_DELETE = "project.delete"
    REPO_CREATE = "repo.create"
    REPO_DELETE = "repo.delete"
    AGENT_RUN = "agent.run"
    DEPLOYMENT_CREATE = "deployment.create"
    DEPLOYMENT_ROLLBACK = "deployment.rollback"
    MCP_INSTALL = "mcp.install"
    SKILL_INSTALL = "skill.install"
    REPO_CONFIGURE = "repo.configure"
    REPO_ARCHIVE = "repo.archive"
    SKILL_REMOVE = "skill.remove"
    MCP_CONFIGURE = "mcp.configure"
    AGENT_CREATE = "agent.create"
    CLAUDE_CONFIGURE = "claude.configure"
    PROJECT_UPDATE = "project.update"
    WORKSPACE_CREATE = "workspace.create"
    WORKSPACE_DELETE = "workspace.delete"
    ENVIRONMENT_CREATE = "environment.create"
    ENVIRONMENT_DELETE = "environment.delete"


class ProjectStatus(StrEnum):
    PROVISIONING = "provisioning"
    PARTIAL = "partial"  # a step failed; re-run `project create` to resume
    ACTIVE = "active"


class AgentPermission(StrEnum):
    """Capabilities an agent may be granted. Least privilege: read-only by default."""

    KNOWLEDGE_READ = "knowledge_read"
    REPO_READ = "repo_read"
    REPO_WRITE = "repo_write"
    RUN_COMMANDS = "run_commands"
    NETWORK = "network"


__all__ = [
    "AgentPermission",
    "ProjectStatus",
    "AuditAction",
    "AuditResult",
    "ExitCode",
    "LogLevel",
    "OutputFormat",
    "Permission",
    "Role",
]
