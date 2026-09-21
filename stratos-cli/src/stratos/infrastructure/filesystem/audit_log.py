"""Local append-only audit log (adapter used until the Platform API provides audit storage)."""

import contextlib
import json
import os
from pathlib import Path

from platformdirs import user_state_dir
from pydantic import ValidationError as PydanticValidationError

from stratos.domain.enums import AuditAction
from stratos.domain.exceptions import DependencyError
from stratos.domain.models.audit import AuditEvent
from stratos.utils.redaction import SecretRedactor


def default_audit_path() -> Path:
    return Path(user_state_dir("stratos", appauthor=False)) / "audit.jsonl"


class LocalAuditStore:
    def __init__(self, path: Path | None = None, redactor: SecretRedactor | None = None) -> None:
        self._path = path or default_audit_path()
        self._redactor = redactor or SecretRedactor()

    def append(self, event: AuditEvent) -> None:
        record = self._redactor.redact(event.model_dump(mode="json"))  # defence in depth
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            with contextlib.suppress(OSError):
                os.chmod(self._path, 0o600)
        except OSError as exc:
            raise DependencyError(f"The audit log cannot be written: {exc.strerror}") from exc

    def _read(self) -> list[AuditEvent]:
        if not self._path.is_file():
            return []
        events: list[AuditEvent] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(AuditEvent.model_validate_json(line))
            except (ValueError, PydanticValidationError):
                continue  # skip corrupt lines rather than failing the whole query
        return events

    def list(
        self, *, limit: int = 50, action: AuditAction | None = None, user: str | None = None
    ) -> list[AuditEvent]:
        events = [
            e
            for e in reversed(self._read())
            if (action is None or e.action == action) and (user is None or e.user == user)
        ]
        return events[:limit]

    def get(self, event_id: str) -> AuditEvent | None:
        return next((e for e in self._read() if e.id == event_id), None)
