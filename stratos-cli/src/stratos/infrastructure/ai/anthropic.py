"""Anthropic implementation of AIProvider. The SDK is imported lazily, only when a call is made."""

from collections.abc import AsyncIterator, Sequence
from typing import Any

from stratos.domain.exceptions import (
    APIError,
    AuthenticationError,
    AuthorizationError,
    NetworkError,
    StratosError,
)
from stratos.domain.models.extensions import (
    AIResponse,
    ChatMessage,
    ChatTurn,
    ModelInfo,
    ToolCall,
    ToolSpec,
)


def _translate(exc: Exception) -> StratosError:
    import anthropic  # lazy

    if isinstance(exc, anthropic.AuthenticationError):
        return AuthenticationError(
            "The AI provider rejected the API key.", hint="Check ANTHROPIC_API_KEY."
        )
    if isinstance(exc, anthropic.PermissionDeniedError):
        return AuthorizationError("The AI provider denied access to this model or action.")
    if isinstance(exc, anthropic.RateLimitError):
        return APIError(
            "The AI provider is rate limiting requests (retries exhausted).",
            hint="Wait a moment and try again.",
        )
    if isinstance(exc, anthropic.APIConnectionError):  # includes timeouts
        return NetworkError("Cannot reach the AI provider.", hint="Check your network connection.")
    status = getattr(exc, "status_code", None)
    return APIError(f"The AI provider returned an error{f' (HTTP {status})' if status else ''}.")


def to_anthropic_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    """Convert neutral messages; consecutive tool results share one user message."""
    out: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            results.append(
                {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
            )
            continue
        if results:
            out.append({"role": "user", "content": results})
            results = []
        if m.role == "assistant" and m.tool_calls:
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            blocks += [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                for c in m.tool_calls
            ]
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": m.role, "content": m.content})
    if results:
        out.append({"role": "user", "content": results})
    return out


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        api_key: str | None,
        *,
        default_model: str,
        max_retries: int = 3,
        client: Any = None,
    ) -> None:
        self._api_key = api_key
        self._default_model = default_model
        self._max_retries = max_retries
        self._client_instance = client

    def _client(self) -> Any:
        if self._client_instance is None:
            if not self._api_key:
                raise AuthenticationError(
                    "No Anthropic API key found.",
                    hint="Set the ANTHROPIC_API_KEY environment variable.",
                )
            import anthropic  # lazy: not loaded for unrelated commands

            self._client_instance = anthropic.AsyncAnthropic(
                api_key=self._api_key, max_retries=self._max_retries
            )
        return self._client_instance

    def _request(
        self, prompt: str, model: str | None, system: str | None, max_tokens: int
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "model": model or self._default_model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            args["system"] = system
        return args

    async def ask(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> AIResponse:
        args = self._request(prompt, model, system, max_tokens)
        client = self._client()
        try:
            resp = await client.messages.create(**args)
        except StratosError:
            raise
        except Exception as exc:
            raise _translate(exc) from exc
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        usage = getattr(resp, "usage", None)
        return AIResponse(
            model=args["model"],
            text=text,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )

    async def stream(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        args = self._request(prompt, model, system, max_tokens)
        client = self._client()
        try:
            async with client.messages.stream(**args) as stream:
                async for chunk in stream.text_stream:
                    yield chunk
        except StratosError:
            raise
        except Exception as exc:
            raise _translate(exc) from exc

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] = (),
        model: str | None = None,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> ChatTurn:
        args: dict[str, Any] = {
            "model": model or self._default_model,
            "max_tokens": max_tokens,
            "messages": to_anthropic_messages(messages),
        }
        if system:
            args["system"] = system
        if tools:
            args["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ]
        client = self._client()
        try:
            resp = await client.messages.create(**args)
        except StratosError:
            raise
        except Exception as exc:
            raise _translate(exc) from exc
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        calls = tuple(
            ToolCall(id=b.id, name=b.name, arguments=dict(b.input or {}))
            for b in resp.content
            if getattr(b, "type", "") == "tool_use"
        )
        usage = getattr(resp, "usage", None)
        return ChatTurn(
            model=args["model"],
            text=text,
            tool_calls=calls,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            stop_reason=getattr(resp, "stop_reason", None),
        )

    async def list_models(self) -> list[ModelInfo]:
        client = self._client()
        try:
            page = await client.models.list(limit=100)
        except StratosError:
            raise
        except Exception as exc:
            raise _translate(exc) from exc
        return [
            ModelInfo(id=m.id, display_name=getattr(m, "display_name", "") or "") for m in page.data
        ]
