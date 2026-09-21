# ADR-014: Cross-cutting engineering (Layer E)

Status: Accepted (21 September 2026)

## Context
Layer E covers operation safety, performance, testing, delivery, documentation and the local development environment. It changes how the CLI is built rather than what it offers.

## Decisions
1. **Operation safety (M24).** All destructive or multi-step operations use `OperationGuard` (dry-run, confirmation, `--yes`) and `Transaction`, which rolls back every recorded write and runs registered undo actions if any step fails. `init` and `skill install` use it. `stratos backup list|restore` lets users undo a Stratos change; a restore first backs up the current file, so it can itself be undone, and is audited (`backup.restore`).
2. **Performance (M25).** `stratos --help` lists commands from a static table and imports no command module and no HTTP client; a test enforces both, and that the table matches each command's real help. A TTL cache holds non-sensitive metadata; `stratos cache status|prune|clear` administers it. Nothing is cached that contains a secret.
3. **Composition root split.** `CliContext` was a 635-line god object. It is now ~300 lines of wiring that delegates to `cli/wiring/{extensions,workflow,diagnostics}.py`. Application code no longer imports infrastructure; architecture tests fail the build if that regresses.
4. **Quality gates (M26).** Unit, integration (`-m integration`) and end-to-end (`-m e2e`, real subprocess) suites; coverage fails below 90% (currently about 91% including branch coverage); strict mypy; ruff; `--strict-markers`.
5. **Delivery (M27).** `uv.lock` is authoritative; CI runs quality, a 3-OS x 2-Python matrix, integration/e2e and a package job that builds the wheel, installs it in a clean venv and runs it (`scripts/verify_package.py`, which also checks the version is semver and matches `__version__`). Releases are tag-driven (`release.yml`).
6. **Documentation (M28).** ADRs for every layer, an implementation plan, a generated CLI reference (a CI check fails on drift) and `openapi.yaml` stays a deliberate placeholder with no endpoints, because inventing a Platform API contract would contradict ADR-009. A test checks it is valid OpenAPI 3.1 and that any path added later declares responses and security.
7. **Local development (M29).** `make setup` bootstraps a venv; `scripts/mock_services.py` provides an offline identity-provider discovery document, health endpoint and demo organisation.

## Open points and how they were resolved
- *Async:* only the HTTP layer offers async clients; the CLI itself is synchronous because commands are short-lived and sequential. Revisit if a long-running command needs concurrency.
- *Ruff security/docstring rule sets:* not enabled; security is covered by CodeQL and pip-audit in `security.yml`, and docstrings are required by review, not by lint.
- *LICENSE:* intentionally left empty pending a company legal decision.

## Consequences
- Contributors must keep the static help table in `cli/app.py` in step with commands (the drift test says how).
- Coverage below 90% fails CI.
