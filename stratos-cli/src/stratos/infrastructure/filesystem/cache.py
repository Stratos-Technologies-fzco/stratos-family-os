"""Small TTL cache for non-sensitive metadata (organisation, skills, MCP registry)."""

import contextlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from platformdirs import user_cache_dir

from stratos.domain.models.files import CacheNamespaceStats
from stratos.utils.redaction import SecretRedactor


class TtlCache:
    def __init__(
        self,
        directory: Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
        redactor: SecretRedactor | None = None,
    ) -> None:
        self._dir = directory or Path(user_cache_dir("stratos", appauthor=False))
        self._clock = clock
        self._redactor = redactor or SecretRedactor()

    def _file(self, namespace: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in namespace)
        return self._dir / f"{safe}.json"

    def _load(self, namespace: str) -> dict[str, Any]:
        try:
            data = json.loads(self._file(namespace).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, namespace: str, key: str) -> Any | None:
        entry = self._load(namespace).get(key)
        if not isinstance(entry, dict) or entry.get("expires", 0) <= self._clock():
            return None
        return entry.get("value")

    def set(self, namespace: str, key: str, value: Any, ttl_seconds: float) -> None:
        if self._redactor.redact(value) != value:
            raise ValueError("Refusing to cache a value that contains a secret.")
        data = {
            k: v for k, v in self._load(namespace).items() if v.get("expires", 0) > self._clock()
        }
        data[key] = {"expires": self._clock() + ttl_seconds, "value": value}
        path = self._file(namespace)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)

    def invalidate(self, namespace: str, key: str | None = None) -> None:
        if key is None:
            self._file(namespace).unlink(missing_ok=True)
            return
        data = self._load(namespace)
        if data.pop(key, None) is not None:
            self._file(namespace).write_text(json.dumps(data), encoding="utf-8")

    def get_or_load(
        self, namespace: str, key: str, ttl_seconds: float, loader: Callable[[], Any]
    ) -> Any:
        cached = self.get(namespace, key)
        if cached is not None:
            return cached
        value = loader()
        try:
            self.set(namespace, key, value, ttl_seconds)
        except ValueError:
            pass  # contains a secret: use it once, never store it
        return value

    # ---- administration (`stratos cache ...`) --------------------------------------------
    def _files(self) -> list[Path]:
        return sorted(self._dir.glob("*.json")) if self._dir.is_dir() else []

    def stats(self) -> list[CacheNamespaceStats]:
        now = self._clock()
        out: list[CacheNamespaceStats] = []
        for path in self._files():
            data = self._load(path.stem)
            expired = sum(1 for v in data.values() if v.get("expires", 0) <= now)
            out.append(CacheNamespaceStats(path.stem, len(data), expired, path.stat().st_size))
        return out

    def prune(self) -> int:
        """Remove expired entries everywhere; returns how many were removed."""
        now = self._clock()
        removed = 0
        for path in self._files():
            data = self._load(path.stem)
            live = {k: v for k, v in data.items() if v.get("expires", 0) > now}
            removed += len(data) - len(live)
            if not live:
                path.unlink(missing_ok=True)
            elif len(live) != len(data):
                path.write_text(json.dumps(live), encoding="utf-8")
        return removed

    def clear(self, namespace: str | None = None) -> int:
        """Delete one namespace (or everything); returns how many entries were removed."""
        removed = 0
        for path in self._files():
            if namespace is None or path.stem == self._file(namespace).stem:
                removed += len(self._load(path.stem))
                path.unlink(missing_ok=True)
        return removed
