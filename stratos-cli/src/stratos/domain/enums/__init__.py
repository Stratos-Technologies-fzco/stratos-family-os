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


__all__ = ["ExitCode", "LogLevel", "OutputFormat"]
