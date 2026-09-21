# ADR-001 to ADR-009: Foundations (Layers A to C)

These records capture decisions made while building the first three layers. They were written retrospectively in Layer E so that every layer has a decision record. Status of all: Accepted.

## ADR-001: Python 3.12+, Typer, Pydantic v2
A single-binary-like CLI with typed configuration. Typer gives consistent help and completion; Pydantic validates configuration and API models. Rejected: argparse (too much boilerplate), Go rewrite (team skills, AI SDK availability).

## ADR-002: Layered architecture with ports and adapters
`domain` (models, enums, ports) -> `utils` -> `config`/`logging` -> `infrastructure` and `application` -> `cli`. Application code depends only on ports (Protocols); `cli/context.py` and `cli/wiring/*` are the only composition root. Enforced by `tests/unit/test_architecture.py`.

## ADR-003: Stable exit codes and error taxonomy
Every failure is a `StratosError` subclass with a fixed exit code (0-10). Usage errors exit 2. No stack traces unless `--debug`.

## ADR-004: Layered configuration
Precedence: CLI flag > environment > project file > user file > organisation file > defaults. Secrets are never stored in these files; only environment-variable references.

## ADR-005: Secrets in the OS keyring only
Tokens live in the operating-system keyring. Logs, audit, cache and configuration pass through a redactor. Caching refuses values that contain a secret.

## ADR-006: Protected file writes
Every write to a developer-owned file goes through `ConfigFileProtector`: backup, diff, merge (JSON/managed blocks) and rollback, with a dry-run mode sharing the same code path.

## ADR-007: Roles and permissions (RBAC)
Four roles (viewer, developer, maintainer, admin) map to permissions checked in application services, never in the CLI layer.

## ADR-008: Audit trail
Sensitive actions are recorded through `AuditService.record` (success, failure and denial), with secrets redacted. Storage is a local append-only JSON-lines file until the Platform API can hold it.

## ADR-009: No invented backend
The Platform API does not exist. Where GitHub has the concept it is the backend; otherwise a local adapter behind a port is used. Nothing is reported as done unless the real system confirmed it.
