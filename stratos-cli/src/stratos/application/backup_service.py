"""Listing and restoring the backups Stratos takes before it changes a file."""

from collections.abc import Callable

from stratos.application.audit_service import AuditService
from stratos.application.safety import OperationGuard
from stratos.domain.enums import AuditAction, Permission
from stratos.domain.exceptions import ResourceNotFoundError, ValidationError
from stratos.domain.interfaces import BackupCatalog, FileProtector
from stratos.domain.models.auth import Identity
from stratos.domain.models.files import BackupEntry, WriteResult

Require = Callable[[Permission], Identity]


class BackupService:
    def __init__(
        self,
        catalog: BackupCatalog,
        protector: FileProtector,
        require: Require,
        audit: AuditService,
        guard: OperationGuard,
    ) -> None:
        self._catalog = catalog
        self._protector = protector
        self._require = require
        self._audit = audit
        self._guard = guard

    def list_backups(self, *, file: str | None = None) -> list[BackupEntry]:
        """Newest first; optionally only backups of files whose name contains `file`."""
        self._require(Permission.ORG_READ)
        entries = self._catalog.list()
        return [e for e in entries if file is None or file in e.original.name]

    def _find(self, ident: str) -> BackupEntry:
        entries = self._catalog.list()
        matches = [e for e in entries if ident in (e.id, e.backup.name, str(e.backup))]
        if not matches:
            raise ResourceNotFoundError(
                f"No backup matches '{ident}'.", hint="See `stratos backup list`."
            )
        if len(matches) > 1:
            raise ValidationError(
                f"'{ident}' matches {len(matches)} backups.",
                hint="Use the full id shown by `stratos backup list`.",
            )
        return matches[0]

    def restore(self, ident: str) -> WriteResult | BackupEntry | None:
        """Restore a backup. The current file is backed up first, so a restore can be undone.
        Returns None in dry-run mode."""
        self._require(Permission.PROJECT_UPDATE)
        entry = self._find(ident)
        impact = [
            f"{entry.original} is replaced with the version from {entry.created}",
            "The current version is backed up first, so this can be undone",
        ]
        if self._guard.preview("backup restore", impact):
            return None
        self._guard.confirm_destructive("Restoring", impact)
        with self._audit.record(AuditAction.BACKUP_RESTORE, "backup", entry.id):
            if entry.kind == "skill":
                self._catalog.restore_skill(entry)
                return entry
            return self._protector.write_bytes(entry.original, self._catalog.read(entry))
