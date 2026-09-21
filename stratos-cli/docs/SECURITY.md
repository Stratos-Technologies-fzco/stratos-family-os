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

## Audit
- Sensitive actions emit events (user, organisation, time, action, resource, resource ID, result, request ID). Failures and denials are recorded. Events pass through the redactor before being written.
- Until the Platform API provides audit storage, events go to a local append-only file (owner-only permissions where the OS supports it).

## Telemetry
None. The CLI sends no usage data.

## Not yet in place
Supply-chain controls (dependency scanning, SAST, secret scanning, SBOM, signed releases) arrive with the CI pipelines (M27); organisation policy and monitoring arrive in Phase 12.
