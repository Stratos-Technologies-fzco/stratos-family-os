"""AI use through the provider abstraction. Signed-in users only; nothing vendor-specific here."""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from stratos.domain.enums import Permission
from stratos.domain.interfaces import AIProvider
from stratos.domain.models.auth import Identity
from stratos.domain.models.extensions import AIResponse, ModelInfo

Require = Callable[[Permission], Identity]


@dataclass(frozen=True)
class AIStatus:
    provider: str
    model: str
    api_key_configured: bool


class AIService:
    def __init__(
        self,
        provider: Callable[[], AIProvider],
        require: Require,
        *,
        provider_name: str,
        default_model: str,
        max_tokens: int,
        api_key_configured: bool,
        check_provider: Callable[[str], None] = lambda provider: None,
        check_model: Callable[[str | None], None] = lambda model: None,
    ) -> None:
        self._provider = provider  # built lazily so the SDK loads only when needed
        self._require = require
        self._name = provider_name
        self._model = default_model
        self._max_tokens = max_tokens
        self._configured = api_key_configured
        self._check_provider = check_provider
        self._check_model = check_model

    def _guard(self, model: str | None) -> None:
        self._require(Permission.ORG_READ)
        self._check_provider(self._name)
        self._check_model(model or self._model)

    def status(self) -> AIStatus:
        self._require(Permission.ORG_READ)
        return AIStatus(self._name, self._model, self._configured)

    async def ask(
        self, prompt: str, *, model: str | None = None, system: str | None = None
    ) -> AIResponse:
        self._guard(model)
        return await self._provider().ask(
            prompt, model=model or self._model, system=system, max_tokens=self._max_tokens
        )

    def stream(
        self, prompt: str, *, model: str | None = None, system: str | None = None
    ) -> AsyncIterator[str]:
        self._guard(model)
        return self._provider().stream(
            prompt, model=model or self._model, system=system, max_tokens=self._max_tokens
        )

    async def models(self) -> list[ModelInfo]:
        self._guard(None)
        return await self._provider().list_models()
