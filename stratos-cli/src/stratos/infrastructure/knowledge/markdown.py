"""Local-folder knowledge sources (Markdown and PDF).

Content is read at query time and never copied into a cache (documents may contain sensitive
text); `sync` stores only a manifest (path, size, modified time, title) used for status.
"""

import contextlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from stratos.domain.exceptions import ConfigurationError
from stratos.domain.models.extensions import KnowledgeDocument, KnowledgeHit, KnowledgeStatus
from stratos.infrastructure.knowledge.scoring import markdown_title, rank
from stratos.utils.redaction import SecretRedactor

MAX_FILES = 5000


class LocalDocumentProvider:
    """Base for sources that are folders of documents. Subclasses set `name`, `pattern`,
    `max_bytes` and override `read_text`/`title_for`."""

    name = "local"
    pattern = "*"
    max_bytes = 1024 * 1024

    def __init__(
        self,
        roots: list[Path],
        manifest_path: Path,
        *,
        redactor: SecretRedactor | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not roots:
            raise ConfigurationError(
                "No knowledge sources are configured.",
                hint="Run `stratos config set knowledge.paths <directory>`.",
            )
        self._labels: dict[str, Path] = {}
        for root in roots:
            label, n = root.name or "root", 2
            while label in self._labels:
                label, n = f"{root.name}-{n}", n + 1
            self._labels[label] = root.resolve()
        self._manifest = manifest_path
        self._redactor = redactor or SecretRedactor()
        self._clock = clock

    # ---- to override ---------------------------------------------------------------------
    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace")

    def title_for(self, text: str, path: Path) -> str:
        return markdown_title(text, path.stem)

    # ---- scanning ------------------------------------------------------------------------
    def _scan(self) -> dict[str, Path]:
        found: dict[str, Path] = {}
        for label, root in self._labels.items():
            if not root.is_dir():
                continue
            for path in sorted(root.rglob(self.pattern)):
                if path.is_symlink() or not path.is_file() or path.stat().st_size > self.max_bytes:
                    continue
                found[f"{label}:{path.relative_to(root).as_posix()}"] = path
                if len(found) >= MAX_FILES:
                    return found
        return found

    # ---- KnowledgeProvider ---------------------------------------------------------------
    async def search(self, query: str, *, limit: int = 5) -> list[KnowledgeHit]:
        docs = []
        for doc_id, path in self._scan().items():
            text = self.read_text(path)
            docs.append((doc_id, self.title_for(text, path), text))
        return rank(query, docs, limit, self._redactor)

    async def get(self, doc_id: str) -> KnowledgeDocument | None:
        path = self._scan().get(doc_id)  # only ids found by scanning: no path traversal possible
        if path is None:
            return None
        text = self.read_text(path)
        return KnowledgeDocument(
            id=doc_id,
            title=self.title_for(text, path),
            content=self._redactor.redact_text(text),
        )

    def _manifest_entries(self) -> dict[str, dict[str, object]]:
        return {
            doc_id: {
                "size": path.stat().st_size,
                "mtime": int(path.stat().st_mtime),
                "title": self.title_for(self.read_text(path), path),
            }
            for doc_id, path in self._scan().items()
        }

    async def sync(self) -> KnowledgeStatus:
        entries = self._manifest_entries()
        synced_at = self._clock().isoformat()
        self._manifest.parent.mkdir(parents=True, exist_ok=True)
        self._manifest.write_text(
            json.dumps({"synced_at": synced_at, "docs": entries}), encoding="utf-8"
        )
        with contextlib.suppress(OSError):
            os.chmod(self._manifest, 0o600)
        return KnowledgeStatus(provider=self.name, documents=len(entries), last_sync=synced_at)

    async def status(self) -> KnowledgeStatus:
        current = self._manifest_entries()
        try:
            saved = json.loads(self._manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return KnowledgeStatus(provider=self.name, documents=len(current), stale=len(current))
        docs = saved.get("docs", {})
        stale = sum(1 for k, v in current.items() if docs.get(k) != v) + sum(
            1 for k in docs if k not in current
        )
        synced = saved.get("synced_at")
        return KnowledgeStatus(
            provider=self.name,
            documents=len(current),
            last_sync=str(synced) if synced else None,
            stale=stale,
        )


class MarkdownKnowledgeProvider(LocalDocumentProvider):
    name = "markdown"
    pattern = "*.md"
