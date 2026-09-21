"""Compatibility re-exports; the content lives in `stratos.domain.standards`."""

__all__ = [
    "CI_PATH",
    "CI_TEMPLATES",
    "CLAUDE_DENY_RULES",
    "DEFAULT_INSTRUCTIONS",
    "DEFAULT_LABELS",
    "GITIGNORE_LINES",
    "HOOK_MARKER",
    "PRE_COMMIT_HOOK",
    "TEMPLATES",
]

from stratos.domain.standards import (  # noqa: E402
    CI_PATH,
    CI_TEMPLATES,
    CLAUDE_DENY_RULES,
    DEFAULT_INSTRUCTIONS,
    DEFAULT_LABELS,
    GITIGNORE_LINES,
    HOOK_MARKER,
    PRE_COMMIT_HOOK,
    TEMPLATES,
)
