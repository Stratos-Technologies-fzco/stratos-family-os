"""Knowledge search across configured sources."""

from collections.abc import Callable

from stratos.domain.enums import Permission
from stratos.domain.exceptions import ResourceNotFoundError
from stratos.domain.interfaces import KnowledgeProvider
from stratos.domain.models.auth import Identity
from stratos.domain.models.extensions import KnowledgeDocument, KnowledgeHit, KnowledgeStatus

Require = Callable[[Permission], Identity]


class KnowledgeService:
    def __init__(self, providers: Callable[[], list[KnowledgeProvider]], require: Require) -> None:
        self._factory = providers  # built lazily: clients are not created for other commands
        self._providers: list[KnowledgeProvider] | None = None
        self._require = require

    def providers(self) -> list[KnowledgeProvider]:
        if self._providers is None:
            self._providers = self._factory()
        return self._providers

    async def aclose(self) -> None:
        """Release network clients held by remote sources."""
        for provider in self._providers or []:
            closer = getattr(provider, "aclose", None)
            if closer is not None:
                await closer()

    async def search(self, query: str, *, limit: int = 5) -> list[KnowledgeHit]:
        self._require(Permission.ORG_READ)
        hits: list[KnowledgeHit] = []
        for provider in self.providers():
            hits += await provider.search(query, limit=limit)
        hits.sort(key=lambda h: (-h.score, h.id))
        return hits[:limit]

    async def get(self, doc_id: str) -> KnowledgeDocument:
        self._require(Permission.ORG_READ)
        for provider in self.providers():
            doc = await provider.get(doc_id)
            if doc is not None:
                return doc
        raise ResourceNotFoundError(f"Knowledge document '{doc_id}' was not found.")

    async def sync(self) -> list[KnowledgeStatus]:
        self._require(Permission.ORG_READ)
        return [await p.sync() for p in self.providers()]

    async def status(self) -> list[KnowledgeStatus]:
        self._require(Permission.ORG_READ)
        return [await p.status() for p in self.providers()]
