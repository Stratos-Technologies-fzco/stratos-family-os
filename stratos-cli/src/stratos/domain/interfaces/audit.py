from datetime import datetime
from typing import Protocol

from stratos.domain.enums import AuditAction
from stratos.domain.models.audit import AuditEvent


class AuditStore(Protocol):
    def append(self, event: AuditEvent) -> None: ...

    def list(
        self,
        *,
        limit: int = 50,
        action: AuditAction | None = None,
        user: str | None = None,
        since: datetime | None = None,
    ) -> list[AuditEvent]:
        """Newest first."""
        ...

    def get(self, event_id: str) -> AuditEvent | None: ...
