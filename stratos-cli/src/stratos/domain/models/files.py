"""Results of file and tool operations shared between services and adapters."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WriteResult:
    """Outcome of one protected write (a backup may exist; `diff` is the unified diff)."""

    path: Path
    changed: bool
    created: bool
    backup: Path | None
    diff: str


@dataclass(frozen=True)
class ClaudeStatus:
    detected: bool
    executable: str | None = None
    version: str | None = None

    def doctor_check(self) -> tuple[str, str]:
        """(level, message) for `stratos doctor`: pass when detected, warning otherwise."""
        if self.detected:
            return "pass", f"Claude Code detected{f' ({self.version})' if self.version else ''}."
        return "warning", "Claude Code was not detected. Install it to use Stratos AI workflows."


@dataclass(frozen=True)
class BackupEntry:
    """A backup Stratos took before changing a file (or removing a skill folder)."""

    id: str  # path of the backup relative to the project
    kind: str  # "file" | "skill"
    original: Path  # where the restored content goes
    backup: Path
    created: str  # ISO timestamp
    size: int


@dataclass(frozen=True)
class CacheNamespaceStats:
    namespace: str
    entries: int
    expired: int
    size_bytes: int
