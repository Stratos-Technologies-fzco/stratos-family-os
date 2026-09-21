# Security

Security is the first-listed platform priority. This document describes the controls that exist today.

## Credentials
- Sign-in uses OpenID Connect / OAuth 2.0 authorization code with **PKCE (S256)** as a public client. No client secret and no password is ever handled or stored.
- Tokens are stored **only in the operating-system keyring** (`infrastructure/secrets`). If no keyring is available the CLI fails with a dependency error; there is deliberately **no plain-text fallback**.
- The login redirect listens on `127.0.0.1` only, on an ephemeral port, and validates the `state` value.
- `stratos auth token` hides the token unless `--reveal` is passed.

## Secrets never leak
- One `SecretRedactor` (`utils/redaction.py`) masks passwords, API keys, tokens, refresh tokens, JWTs, Bearer values and private keys. It is applied to logs, rendered output, error messages, API error details and audit events.
- Configuration files (YAML) and environment-derived settings reject secret-looking keys.
- Token values are `SecretStr` in the domain model and never appear in `repr` or JSON dumps.

## Network
- TLS is mandatory for the identity provider, the Platform API and the discovered OIDC endpoints. Plain `http` is accepted only for `localhost` (development).
- The API client never follows redirects and refuses pagination links that point to a different host, so a bearer token cannot be sent elsewhere.
- Requests carry request and correlation IDs. Destructive calls are not retried automatically unless an idempotency key is supplied.

## Access control (RBAC)
- Roles: `viewer`, `developer`, `maintainer`, `admin` (read from the identity provider's `roles` or `groups` claim). Unknown or missing roles grant only `viewer` (least privilege).
- Sensitive commands call `CliContext.require(Permission)` before acting; a refusal exits with code 4 and is audited as `denied`.
- The role-to-permission matrix (`application/authorization.py`) is a proposed default pending organisation policy (Phase 12).

## Input and output safety
- `validate_slug` restricts names to lowercase letters, digits, `.`, `_`, `-`.
- All rendered text is stripped of terminal escape sequences and control characters.

## Integrations (Layer C)
- **Skills** are checked (checksum, compatibility, declared permissions) before anything is written, need approval for declared permissions, and are copied, never executed. Paths that try to escape the skill folder are rejected.
- **MCP servers** are installed only if organisation policy allows them. Secrets are never written to `.mcp.json`: sensitive settings must be `${VAR}` references; secret-looking arguments, unsafe commands and unsupported transports are rejected.
- **Configuration files** owned by developers (`.mcp.json`, `.claude/settings.json`, `CLAUDE.md`) are merged or edited only inside a Stratos block, backed up first, and can be rolled back.
- **Agents** run with least privilege: an agent's permissions must be allowed by `agents.allowed_permissions` (default read-only). Input is stripped of control characters, size-limited and passed as data, not instructions. Run logs are redacted.
- **Knowledge** documents are read at query time and never copied into a cache; results are redacted and lookups are confined to the configured folders.
- **GitHub** calls validate names, respect rate limits, and never place a token in a URL or a clone command. Destructive actions (archive, public repository) need confirmation; every change supports `--dry-run`.
- **Cache** never stores a value that contains a secret.
- **Agent tools** are read-only and confined to the project folder: `.env`, keys, `.git`, `.mcp.json`, virtual environments and paths outside the project are blocked; output is redacted and size-limited. Every tool call is re-checked against the agent's permissions, and a step limit stops runaway loops.
- **MCP servers run only when** installed, allowed by `mcp.allowed`, and the agent holds `run_commands` (not allowed by default). They receive a minimal environment (never the caller's whole environment), have time limits, and are always terminated after the run.
- **Skills** used by an agent are added to its instructions; they are never executed as code.
- **Claude subagents** exported from agents get read-only tools unless the agent's permissions grant more; developer-authored subagent files are never overwritten.
- **Remote knowledge** (GitHub, Confluence, SharePoint) is read live and never cached; credentials come from the environment and are never written to configuration. SharePoint downloads use pre-authenticated HTTPS URLs without sending credentials.

## Developer workflow (Layer D)
- **Project creation** commits only verified content: skills are checksum- and compatibility-checked, MCP servers are validated (no secrets, references only) and policy-checked, existing files are preserved via managed blocks and merging.
- **`stratos init`** backs up every file it changes, shows a diff, rolls everything back if a step fails, and adds `.env` deny rules to `.claude/settings.json` (keeping any rules already there).
- **Workspaces** never install dependencies unless asked, disable Node install scripts, never overwrite `.env` or an existing git hook, and install a pre-commit hook that blocks `.env` files and obvious credentials. Deleting files refuses uncommitted work (unless `--force`) and anything that is not a git working copy.
- **Deployments** confirm production deploys, rollbacks and deletions, are never retried automatically, use protected-branch environments for staging and production where the plan allows, and never report success themselves; they show the statuses the pipeline posts.
- **`doctor`** reports credential variable *names* only. `--online` probes send no credentials.

## Organisation policy and monitoring
- `policy.allow_public_repos`, `policy.allowed_ai_providers`, `policy.allowed_ai_models`, `mcp.allowed` and `agents.allowed_permissions` are enforced in services (normally set in the organisation configuration layer). Refusals exit with code 4 and are audited as `denied`.
- `stratos audit summary` gives usage analytics and alerts (many denials, high failure rate, a user repeatedly denied); thresholds are `monitoring.*`.

## Supply chain (CI)
Dependency audit (`pip-audit`), pull-request dependency review, CodeQL static analysis, secret scanning (gitleaks; organisation repositories need a `GITLEAKS_LICENSE` secret), a CycloneDX SBOM on every push and release, and a release pipeline that signs build provenance. Pin workflow actions to commit SHAs once the repository is final.

## Audit
- Sensitive actions emit events (user, organisation, time, action, resource, resource ID, result, request ID). Failures and denials are recorded. Events pass through the redactor before being written.
- Until the Platform API provides audit storage, events go to a local append-only file (owner-only permissions where the OS supports it).

## Telemetry
None. The CLI sends no usage data.

## Not yet in place
Supply-chain controls (dependency scanning, SAST, secret scanning, SBOM, signed releases) arrive with the CI pipelines (M27); organisation policy and monitoring arrive in Phase 12.
