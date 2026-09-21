# ADR-010: Security model for authentication, access and audit

Status: Accepted (Layer B, 21 September 2026)

## Context
The CLI must sign users in with an enterprise identity provider, call a Platform API that does not exist yet, enforce least-privilege access and record sensitive actions, without inventing backend behaviour.

## Decisions
1. **OIDC + PKCE public client, loopback redirect.** No secrets to protect on the client; works with Entra ID, Okta, Google Workspace and other compliant issuers via discovery.
2. **Keyring only.** Tokens are stored in the OS keyring, chunked to fit Windows credential limits. No file fallback.
3. **Session metadata is separate from tokens.** Identity and expiry are stored alongside but are not secrets; tokens are `SecretStr`.
4. **RBAC in the application layer.** Commands stay thin and call `require(permission)`; roles come from the identity provider. Default matrix is a proposal.
5. **Sync HTTP client for now.** `httpx.Client` with an injectable transport. An async variant will be added when parallel network work is needed.
6. **Retry policy.** 429 is retried for any method (request not processed); 502/503/504 and network errors only for safe methods or when an idempotency key is supplied.
7. **Pagination convention.** RFC 8288 `Link: rel="next"` with array bodies, pending the published API contract (`docs/openapi.yaml`).
8. **Audit via a port.** `AuditStore` protocol with a local JSONL adapter today; a Platform API adapter replaces it later without changing callers.

## Consequences
- Login needs `auth.issuer` and `auth.client_id` configured; nothing else is assumed about the backend.
- Adopting a different role model or API pagination scheme changes one module each.
