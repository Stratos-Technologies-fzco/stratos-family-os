"""Operation safety (M24): dry run, confirmation and idempotency helpers shared by every
mutating command."""

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from stratos.domain.exceptions import OperationCancelledError
from stratos.domain.interfaces import Renderer


def idempotency_key(*parts: str) -> str:
    """Deterministic key: the same operation on the same resource always yields the same key."""
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]


@dataclass(frozen=True)
class SafetyOptions:
    dry_run: bool = False
    assume_yes: bool = False  # --yes: skip confirmation prompts (automation)


class OperationGuard:
    def __init__(
        self,
        renderer: Renderer,
        options: SafetyOptions | None = None,
        confirm: Callable[[str], bool] | None = None,
    ) -> None:
        self._renderer = renderer
        self.options = options or SafetyOptions()
        self._confirm = confirm or (lambda message: renderer.confirm(message, default=False))

    def preview(self, title: str, steps: Sequence[str]) -> bool:
        """In dry-run mode print what would happen and return True (caller must stop)."""
        if not self.options.dry_run:
            return False
        self._renderer.warning("DRY RUN")
        self._renderer.info(f"{title} would:")
        for step in steps:
            self._renderer.info(f"  - {step}")
        self._renderer.info("No changes were made.")
        return True

    def confirm(self, heading: str, lines: Sequence[str]) -> None:
        """Show `lines` and ask `Continue? [y/N]`. Cancelling raises OperationCancelledError."""
        if self.options.assume_yes:
            return
        self._renderer.warning(heading)
        for item in lines:
            self._renderer.info(f"  - {item}")
        if not self._confirm("Continue?"):
            raise OperationCancelledError("Operation cancelled. No changes were made.")

    def confirm_destructive(self, action: str, impact: Sequence[str]) -> None:
        self.confirm(f"{action} is destructive. This will:", impact)
