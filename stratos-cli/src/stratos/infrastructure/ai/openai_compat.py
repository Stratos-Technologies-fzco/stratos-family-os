"""OpenAI and Azure OpenAI implementations of AIProvider. The SDK loads lazily and is optional
(`pip install stratos-cli[openai]`). For Azure, `model` means the deployment name."""

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from stratos.domain.exceptions import (
    APIError,
    AuthenticationError,
    AuthorizationError,
    DependencyError,
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


def _translate(exc: Exception, key_hint: str) -> StratosError:
    import openai  # lazy

    if isinstance(exc, openai.AuthenticationError):
        return AuthenticationError("The AI provider rejected the API key.", hint=key_hint)
    if isinstance(exc, openai.PermissionDeniedError):
        return AuthorizationError("The AI provider denied access to this model or action.")
    if isinstance(exc, openai.RateLimitError):
        return APIError(
            "The AI provider is rate limiting requests (retries exhausted).",
            hint="Wait a moment and try again.",
        )
    if isinstance(exc, openai.APIConnectionError):  # includes timeouts
        return NetworkError("Cannot reach the AI provider.", hint="Check your network connection.")
    status = getattr(exc, "status_code", None)
    return APIError(f"The AI provider returned an error{f' (HTTP {status})' if status else ''}.")


def to_openai_messages(messages: Sequence[ChatMessage], system: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        elif m.role == "assistant" and m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in m.tool_calls
                    ],
                }
            )
        else:
            out.append({"role": m.role, "content": m.content})
    return out


class OpenAIProvider:
    name = "openai"
    _key_hint = "Check OPENAI_API_KEY."

    def __init__(
        self, api_key: str | None, *, default_model: str, max_retries: int = 3, client: Any = None
    ) -> None:
        self._api_key = api_key
        self._default_model = default_model
        self._max_retries = max_retries
        self._client_instance = client

    def _make_client(self, openai_module: Any) -> Any:
        return openai_module.AsyncOpenAI(api_key=self._api_key, max_retries=self._max_retries)

    def _client(self) -> Any:
        if self._client_instance is None:
            if not self._api_key:
                raise AuthenticationError(
                    "No API key found.", hint=self._key_hint.replace("Check", "Set")
                )
            try:
                import openai
            except ImportError as exc:
                raise DependencyError(
                    "The OpenAI SDK is not installed.",
                    hint="Install it with `pip install stratos-cli[openai]`.",
                ) from exc
            self._client_instance = self._make_client(openai)
        return self._client_instance

    def _request(
        self, messages: list[dict[str, Any]], model: str | None, max_tokens: int
    ) -> dict[str, Any]:
        return {
            "model": model or self._default_model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
        }

    async def _create(self, args: dict[str, Any]) -> Any:
        client = self._client()
        try:
            return await client.chat.completions.create(**args)
        except StratosError:
            raise
        except Exception as exc:
            raise _translate(exc, self._key_hint) from exc

    async def ask(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> AIResponse:
        turn = await self.chat(
            [ChatMessage(role="user", content=prompt)],
            model=model,
            system=system,
            max_tokens=max_tokens,
        )
        return AIResponse(
            model=turn.model,
            text=turn.text,
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
        )

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] = (),
        model: str | None = None,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> ChatTurn:
        args = self._request(to_openai_messages(messages, system), model, max_tokens)
        if tools:
            args["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in tools
            ]
        resp = await self._create(args)
        choice = resp.choices[0]
        calls: list[ToolCall] = []
        for c in getattr(choice.message, "tool_calls", None) or []:
            try:
                arguments = json.loads(c.function.arguments or "{}")
            except ValueError:
                arguments = {}
            calls.append(ToolCall(id=c.id, name=c.function.name, arguments=arguments))
        usage = getattr(resp, "usage", None)
        return ChatTurn(
            model=args["model"],
            text=choice.message.content or "",
            tool_calls=tuple(calls),
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            stop_reason=getattr(choice, "finish_reason", None),
        )

    async def stream(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        args = self._request(
            to_openai_messages([ChatMessage(role="user", content=prompt)], system),
            model,
            max_tokens,
        )
        args["stream"] = True
        events = await self._create(args)
        try:
            async for chunk in events:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except StratosError:
            raise
        except Exception as exc:
            raise _translate(exc, self._key_hint) from exc

    async def list_models(self) -> list[ModelInfo]:
        client = self._client()
        try:
            page = await client.models.list()
        except StratosError:
            raise
        except Exception as exc:
            raise _translate(exc, self._key_hint) from exc
        return [ModelInfo(id=m.id, display_name=m.id) for m in page.data]


class AzureOpenAIProvider(OpenAIProvider):
    name = "azure-openai"
    _key_hint = "Check AZURE_OPENAI_API_KEY."

    def __init__(
        self,
        api_key: str | None,
        *,
        endpoint: str | None,
        api_version: str,
        default_model: str,
        max_retries: int = 3,
        client: Any = None,
    ) -> None:
        super().__init__(
            api_key, default_model=default_model, max_retries=max_retries, client=client
        )
        self._endpoint = endpoint
        self._api_version = api_version

    def _make_client(self, openai_module: Any) -> Any:
        if not self._endpoint:
            raise APIError(
                "No Azure OpenAI endpoint is configured.",
                hint="Run `stratos config set ai.azure_endpoint https://<resource>.openai.azure.com`.",
            )
        return openai_module.AsyncAzureOpenAI(
            api_key=self._api_key,
            azure_endpoint=self._endpoint,
            api_version=self._api_version,
            max_retries=self._max_retries,
        )
