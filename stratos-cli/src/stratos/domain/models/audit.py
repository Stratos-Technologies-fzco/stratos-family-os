from datetime import datetime

from stratos.domain.enums import AuditAction, AuditResult
from stratos.domain.models import StratosModel


class AuditEvent(StratosModel):
    """One record of who did what. Must never contain secrets."""

    id: str
    timestamp: datetime
    user: str
    organisation: str | None = None
    action: AuditAction
    resource: str
    resource_id: str | None = None
    result: AuditResult
    request_id: str
    details: dict[str, str] = {}
