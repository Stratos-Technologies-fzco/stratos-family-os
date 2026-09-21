# Architecture

## Layers

```
Typer command  ->  Application service  ->  Port (domain interface)  ->  Infrastructure adapter  ->  External service
  cli/commands       application/            domain/interfaces           infrastructure/
```

- **`cli/`** parses options and renders. It holds no business rules. `CliContext` builds services lazily, so `stratos --help` creates no clients and unrelated commands never load AI, GitHub or knowledge code.
- **`application/`** owns the rules: permission checks (`CliContext.require`), organisation policy, dry run and confirmation (`OperationGuard`), idempotency, audit (`AuditService.record`).
- **`domain/`** holds typed models, enums, the exception hierarchy and the ports (`GitHubPort`, `AIProvider`, `KnowledgeProvider`, `SecretStore`, `AuditStore`, ...). It performs no I/O.
- **`infrastructure/`** implements the ports: `api/` (sync and async HTTP clients), `auth/` (OIDC), `secrets/` (keyring), `github/`, `ai/`, `knowledge/`, `mcp/`, `claude/`, `filesystem/`.
- **`config/`** layers settings: CLI > environment > project > user > organisation defaults.
- **`utils/`** is the shared `SecretRedactor`, validation and sanitisation, PKCE, HTML-to-text.

## Modules

| Layer | Modules |
|---|---|
| A Foundation | M01 shell, M02 output, M03 configuration, M04 errors and exit codes, M05 logging and redaction, M06 domain core |
| B Connectivity and trust | M07 API client (sync and async), M08 authentication, M09 access control and policy, M10 audit, analytics and monitoring |
| C Integrations | M11 GitHub, M12 organisation and teams, M13 Claude Code, M14 skills, M15 MCP, M16 AI providers, M17 agents, M18 knowledge |
| E cross-cutting (built as needed) | M23 diagnostics, M24 operation safety, M25 caching, M27 CI/CD |

## Key flows

- **Sign-in:** OIDC authorization code with PKCE, loopback redirect on `127.0.0.1`, state check, tokens chunked into the OS keyring, refresh on expiry.
- **Guarded change:** `require(permission)` -> policy check -> existence check (idempotency) -> `--dry-run` preview -> confirmation -> `audit.record(...)` around the write -> backup and rollback for files.
- **Agent run:** permission and policy checks -> system prompt (instructions + installed skills) -> knowledge context -> bounded tool loop (each call re-checked against the agent's permissions) -> redacted run log. MCP servers start only if installed, allowed by policy and the agent holds `run_commands`, and always shut down.
- **Retries:** 429 and GitHub primary rate limits are retried for any method (honouring `Retry-After` / `X-RateLimit-Reset`); 502/503/504 and network errors only for safe methods or with an idempotency key; destructive calls are never retried blindly.

## Extending

- New AI vendor: implement `AIProvider` (see `infrastructure/ai/openai_compat.py`) and register it in `infrastructure/ai/__init__.py`.
- New knowledge source: implement `KnowledgeProvider` (`search`, `get`, `sync`, `status`, optional `aclose`) and add it to `CliContext._knowledge_providers`.
- New command group: add a module under `cli/commands/`, register it in `LAZY_GROUPS` (`cli/app.py`).

Decisions are recorded in [`adr/`](adr/).
