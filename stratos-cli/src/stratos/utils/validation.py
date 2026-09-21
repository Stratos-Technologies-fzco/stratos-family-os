"""Input validation and output sanitisation."""

import re
from typing import Any
from urllib.parse import urlsplit

from stratos.domain.exceptions import ConfigurationError, ValidationError

_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,98}[a-z0-9])?$")
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def validate_slug(value: str, field: str = "name") -> str:
    """Lowercase letters, digits, '.', '_' and '-'; 1-100 chars; no leading/trailing punctuation."""
    if not _SLUG.match(value):
        raise ValidationError(
            f"Invalid {field} '{value[:60]}'.",
            hint="Use 1-100 lowercase letters, digits, '.', '_' or '-'.",
        )
    return value


def sanitize_text(value: str) -> str:
    """Strip terminal escape sequences and control characters (keeps newline and tab)."""
    return _CONTROL.sub("", _ANSI.sub("", value))


def sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, tuple):
        return tuple(sanitize(v) for v in value)
    return value


def require_secure_url(url: str, field: str) -> str:
    """TLS is mandatory; plain http is allowed only for loopback (local development)."""
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return url
    if parts.scheme == "http" and parts.hostname in _LOOPBACK:
        return url
    raise ConfigurationError(
        f"{field} must be an https URL (got '{sanitize_text(url)[:80]}').",
        hint="Plain http is only allowed for localhost.",
    )
