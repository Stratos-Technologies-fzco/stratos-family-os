# Stratos CLI

Command-line interface for the **Stratos Enterprise Developer Platform**: sign in with your organisation account, create and govern GitHub repositories, install approved skills and MCP servers, ask AI models, run review agents, and search company knowledge, with security, audit and dry-run safety built in.

## Install

```bash
pip install -e ".[openai,pdf]"     # extras are optional (OpenAI/Azure providers, PDF knowledge)
stratos --help
```

Requires Python 3.12+. Run `stratos doctor` to check your machine.

## First steps

```bash
stratos config set auth.issuer https://login.example.com
stratos config set auth.client_id <client-id>
stratos login                       # browser sign-in (OIDC + PKCE); tokens go to the OS keyring
stratos whoami

stratos repo create my-service --dry-run     # preview
stratos repo create my-service               # safe to re-run
stratos repo configure my-service --policy --security --actions
stratos knowledge search "how do we roll back a deployment"
stratos agent run code-review --input path/to/change.diff
```

Every command supports `-o json|yaml|plain|table|quiet`. Mutating commands support `--dry-run` and confirm before anything destructive (`--yes` for automation). See [`docs/CLI_REFERENCE.md`](docs/CLI_REFERENCE.md).

## Principles

- **Secrets never leave safe places.** Tokens live only in the OS keyring; secrets are redacted from output, logs, audit events and caches; nothing sensitive is written into shared configuration.
- **Least privilege.** Role-based access, agent permissions that default to read-only, organisation policy (`policy.*`, `mcp.allowed`, `agents.allowed_permissions`).
- **Safe changes.** Idempotent creates, dry run, confirmation, backups and rollback for configuration files, audit trail for sensitive actions.
- **No telemetry.**

## Documentation

[Architecture](docs/ARCHITECTURE.md) · [Security](docs/SECURITY.md) · [CLI reference](docs/CLI_REFERENCE.md) · [SDK / API](docs/API.md) · [Development](docs/DEVELOPMENT.md) · [Contributing](docs/CONTRIBUTING.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [Decisions](docs/adr/)

## Status

Foundation, connectivity, integrations, governance and CI are implemented. The Stratos Platform API does not exist yet: the SDK is built against standard OIDC/HTTP conventions and local adapters, and does not invent backend behaviour. The project/workspace/deployment workflow modules (Layer D) are next.
