"""Transaction boundary for multi-step changes: undo everything if any step fails."""

from collections.abc import Callable
from types import TracebackType

from stratos.domain.interfaces import FileProtector
from stratos.domain.models.files import WriteResult


class Transaction:
    """Collects the effects of a multi-step operation.

    Use as a context manager: if the block raises, every recorded file write is rolled back (in
    reverse order) and every registered undo action runs; on success nothing is undone.
    """

    def __init__(self, protector: FileProtector) -> None:
        self._protector = protector
        self._writes: list[WriteResult] = []
        self._undo: list[Callable[[], None]] = []

    def record(self, result: WriteResult) -> WriteResult:
        """Remember a protected write so it can be rolled back."""
        self._writes.append(result)
        return result

    def on_rollback(self, undo: Callable[[], None]) -> None:
        """Register a compensating action for a step that is not a file write."""
        self._undo.append(undo)

    @property
    def writes(self) -> list[WriteResult]:
        return list(self._writes)

    def rollback(self) -> None:
        for undo in reversed(self._undo):
            undo()
        for result in reversed(self._writes):
            self._protector.rollback(result)
        self._undo.clear()
        self._writes.clear()

    def __enter__(self) -> "Transaction":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.rollback()
