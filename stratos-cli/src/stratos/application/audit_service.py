"""Audit events for sensitive commands. Events never contain secrets."""

import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from stratos.domain.enums import AuditAction, AuditResult
from stratos.domain.exceptions import AuthorizationError, ResourceNotFoundError
from stratos.domain.interfaces import AuditStore
from stratos.domain.models.audit import AuditEvent, AuditSummary
from stratos.domain.models.auth import Identity
from stratos.logging import get_correlation_ids
from stratos.utils.redaction import SecretRedactor


class AuditService:
    def __init__(
        self,
        store: AuditStore,
        identity: Callable[[], Identity | None],
        *,
        redactor: SecretRedactor | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        request_id: Callable[[], str] = lambda: get_correlation_ids()[0],
    ) -> None:
        self._store = store
        self._identity = identity
        self._redactor = redactor or SecretRedactor()
        self._clock = clock
        self._request_id = request_id

    def emit(
        self,
        action: AuditAction,
        resource: str,
        resource_id: str | None = None,
        *,
        result: AuditResult,
        details: Mapping[str, object] | None = None,
    ) -> AuditEvent:
        who = self._identity()
        clean = self._redactor.redact({k: str(v) for k, v in (details or {}).items()})
        event = AuditEvent(
            id=uuid.uuid4().hex,
            timestamp=self._clock(),
            user=who.display if who else "anonymous",
            organisation=who.organisation if who else None,
            action=action,
            resource=resource,
            resource_id=resource_id,
            result=result,
            request_id=self._request_id(),
            details=clean,
        )
        self._store.append(event)
        return event

    @contextmanager
    def record(
        self,
        action: AuditAction,
        resource: str,
        resource_id: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> Iterator[None]:
        """Audit an operation: success if the block completes, otherwise denied or failure."""
        try:
            yield
        except AuthorizationError:
            self.emit(action, resource, resource_id, result=AuditResult.DENIED, details=details)
            raise
        except BaseException:
            self.emit(action, resource, resource_id, result=AuditResult.FAILURE, details=details)
            raise
        self.emit(action, resource, resource_id, result=AuditResult.SUCCESS, details=details)

    def list_events(
        self,
        *,
        limit: int = 50,
        action: AuditAction | None = None,
        user: str | None = None,
        since: datetime | None = None,
    ) -> list[AuditEvent]:
        return self._store.list(limit=limit, action=action, user=user, since=since)

    def summary(
        self,
        *,
        days: int,
        denied_threshold: int = 5,
        failure_rate_threshold: float = 0.25,
        min_events_for_rate: int = 5,
    ) -> AuditSummary:
        """Usage analytics plus alerts (many denials, a high failure rate, one noisy user)."""
        since = self._clock() - timedelta(days=days)
        events = self._store.list(limit=1_000_000, since=since)
        by_action = Counter(e.action.value for e in events)
        by_result = Counter(e.result.value for e in events)
        by_user = Counter(e.user for e in events)
        failed = by_result.get("failure", 0)
        rate = round(failed / len(events), 3) if events else 0.0
        alerts: list[str] = []
        denied = by_result.get("denied", 0)
        if denied >= denied_threshold:
            alerts.append(f"{denied} denied actions in the last {days} days")
        if len(events) >= min_events_for_rate and rate > failure_rate_threshold:
            alerts.append(f"failure rate {rate:.0%} exceeds {failure_rate_threshold:.0%}")
        denied_by_user = Counter(e.user for e in events if e.result is AuditResult.DENIED)
        for user, count in sorted(denied_by_user.items()):
            if count >= denied_threshold:
                alerts.append(f"{user} was denied {count} times")
        return AuditSummary(
            since=since,
            total=len(events),
            by_action=dict(by_action),
            by_result=dict(by_result),
            by_user=dict(by_user),
            failure_rate=rate,
            alerts=tuple(alerts),
        )

    def get_event(self, event_id: str) -> AuditEvent:
        event = self._store.get(event_id)
        if event is None:
            raise ResourceNotFoundError(f"No audit event '{event_id}'.")
        return event
