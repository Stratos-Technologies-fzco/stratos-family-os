"""Central secret redaction, shared by logging, output and error handling."""

import re
from typing import Any

REDACTED = "[REDACTED]"

_SENSITIVE_KEY = re.compile(
    r"(pass(word|wd)?|secret|token|api[_-]?key|private[_-]?key|credential|authorization)",
    re.IGNORECASE,
)
_KEY_VALUE = re.compile(
    r"""(\b(?:pass(?:word|wd)?|secret|token|api[_-]?key|refresh[_-]?token)\s*[=:]\s*)("[^"]*"|'[^']*'|[^\s,;&]+)""",
    re.IGNORECASE,
)
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),  # JWT
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
)


_ENV_REF = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")


def is_env_reference(value: object) -> bool:
    """`${NAME}` points at an environment variable; it is not a secret itself."""
    return isinstance(value, str) and bool(_ENV_REF.match(value))


def is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY.search(key))


class SecretRedactor:
    """Masks secrets in strings and nested structures."""

    def redact_text(self, text: str) -> str:
        for pattern in _PATTERNS:
            text = pattern.sub(REDACTED, text)
        return _KEY_VALUE.sub(lambda m: f"{m.group(1)}{REDACTED}", text)

    def redact(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, dict):
            return {k: self._redact_item(k, v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self.redact(v) for v in value)
        return value

    def _redact_item(self, key: object, value: Any) -> Any:
        masked = (
            isinstance(key, str)
            and is_sensitive_key(key)
            and isinstance(value, str)
            and value != ""
            and not is_env_reference(value)
        )
        return REDACTED if masked else self.redact(value)
