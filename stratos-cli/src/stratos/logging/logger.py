"""Structured logging with correlation IDs and mandatory secret redaction.

No telemetry is collected or sent; logs go to stderr only.
"""

import json
import logging
import sys
import uuid
from contextvars import ContextVar

from stratos.domain.enums import LogLevel
from stratos.utils.redaction import SecretRedactor

ROOT = "stratos"
_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def set_correlation(request_id: str | None = None, correlation_id: str | None = None) -> None:
    _request_id.set(request_id or new_id())
    _correlation_id.set(correlation_id or new_id())


def get_correlation_ids() -> tuple[str, str]:
    """(request_id, correlation_id) for the current invocation."""
    return _request_id.get(), _correlation_id.get()


class _RedactingFilter(logging.Filter):
    def __init__(self, redactor: SecretRedactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redactor.redact_text(record.getMessage())
        record.args = None
        return True


class _Formatter(logging.Formatter):
    def __init__(self, *, as_json: bool) -> None:
        super().__init__()
        self._json = as_json

    def format(self, record: logging.LogRecord) -> str:
        fields = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": _request_id.get(),
            "correlation_id": _correlation_id.get(),
        }
        if self._json:
            return json.dumps(fields)
        return (
            f"{fields['time']} {fields['level']:<7} {fields['logger']} "
            f"[req={fields['request_id']} corr={fields['correlation_id']}] {fields['message']}"
        )


def level_for(verbose: bool, debug: bool) -> LogLevel:
    if debug:
        return LogLevel.DEBUG
    return LogLevel.INFO if verbose else LogLevel.WARNING


def configure_logging(
    level: LogLevel = LogLevel.WARNING,
    *,
    redactor: SecretRedactor | None = None,
    as_json: bool = False,
) -> logging.Logger:
    """Configure the `stratos` logger (idempotent)."""
    logger = logging.getLogger(ROOT)
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_Formatter(as_json=as_json))
    handler.addFilter(_RedactingFilter(redactor or SecretRedactor()))
    logger.addHandler(handler)
    logger.setLevel(level.value)
    logger.propagate = False
    set_correlation()
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT}.{name}")
