# SDK and API

## Status of the Platform API

The Stratos Platform API does not exist yet, so **no platform endpoints are defined or assumed**. `docs/openapi.yaml` is a placeholder until a contract is published. What exists is the reusable SDK layer every integration goes through.

## HTTP clients

`stratos.infrastructure.api.client.PlatformApiClient` (sync) and `stratos.infrastructure.api.async_client.AsyncPlatformApiClient` (async) share one policy:

- `https://` only (plain `http` for localhost); no redirects; pagination follows RFC 8288 `Link: rel="next"` and never leaves the API's host (so bearer tokens cannot be sent elsewhere).
- Token supplied by an injected callable; `auth_scheme` is `Bearer` (default) or `Basic`.
- `X-Request-ID` on every call, `X-Correlation-ID` optional.
- Bounded retries with exponential backoff: 429 and GitHub-style primary rate limits (honouring `Retry-After` and `X-RateLimit-Reset`, up to 60 s) for any method; 502/503/504 and network errors only for GET/HEAD/OPTIONS or when an `idempotency_key` is given.
- Errors map to the exception hierarchy (`AuthenticationError`, `AuthorizationError`, `ResourceNotFoundError`, `ValidationError`, `NetworkError`, `APIError`) with redacted messages and the request id.

```python
from stratos.infrastructure.api.client import PlatformApiClient

with PlatformApiClient("https://api.example.com", token_provider) as api:
    thing = api.get("/v1/things/42")
    for item in api.paginate("/v1/things", limit=100):
        ...
```

```python
from stratos.infrastructure.api.async_client import AsyncPlatformApiClient

async with AsyncPlatformApiClient("https://api.github.com", token_provider) as api:
    page = await api.get("/orgs/acme")
```

## Testing against a mock

`stratos.infrastructure.api.mock.mock_transport({("GET", "/path"): response_or_handler})` returns an `httpx.MockTransport` accepted by both clients.

## Ports

Applications depend on ports in `stratos.domain.interfaces`: `GitHubPort`, `AIProvider` (`ask`, `stream`, `chat` with tool calls, `list_models`), `KnowledgeProvider`, `SkillRegistry`, `McpRegistry`, `AgentRegistry`, `AuditStore`, `SecretStore`, `IdentityProvider`.

## Registry formats (local adapters)

- **Skills** `index.json`: `{"skills": [{"name", "version", "description", "source", "checksum", "permissions": [], "compatibility": {"stratos": ">=0.1.0"}}]}` plus one folder per skill. `checksum` is the value of `compute_checksum(files)` in `infrastructure/filesystem/skill_registry.py`.
- **MCP** `index.json`: `{"servers": [{"name", "description", "command", "args": [], "env": {"KEY": "${ENV_VAR}"}, "transport": "stdio"}]}`.
- **Agents**: `.stratos/agents/<name>.yaml` (`name`, `description`, `instructions`, `tools`, `skills`, `mcp_servers`, `knowledge_sources`, `permissions`).
