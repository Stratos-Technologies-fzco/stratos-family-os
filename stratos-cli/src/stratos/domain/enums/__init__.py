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


__all__ = [
    "AuditAction",
    "AuditResult",
    "ExitCode",
    "LogLevel",
    "OutputFormat",
    "Permission",
    "Role",
]
