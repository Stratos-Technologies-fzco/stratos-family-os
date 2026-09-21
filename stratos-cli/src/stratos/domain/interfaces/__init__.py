from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class Renderer(Protocol):
    """Port for all user-facing output (implemented by cli/output.py)."""

    def data(
        self,
        rows: Sequence[Mapping[str, Any]] | Mapping[str, Any],
        *,
        title: str | None = None,
    ) -> None: ...

    def success(self, message: str) -> None: ...

    def info(self, message: str) -> None: ...

    def warning(self, message: str) -> None: ...

    def error(self, message: str, *, hint: str | None = None) -> None: ...

    def debug(self, message: str) -> None: ...

    def confirm(self, message: str, *, default: bool = False) -> bool: ...


from stratos.domain.interfaces.audit import AuditStore  # noqa: E402
from stratos.domain.interfaces.identity import (  # noqa: E402
    AccessTokenProvider,
    CallbackReceiver,
    IdentityProvider,
    SecretStore,
)

__all__ = [
    "AccessTokenProvider",
    "AuditStore",
    "CallbackReceiver",
    "IdentityProvider",
    "Renderer",
    "SecretStore",
]
