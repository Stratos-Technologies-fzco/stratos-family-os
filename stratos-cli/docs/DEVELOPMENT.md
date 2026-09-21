# Development

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows;  source .venv/bin/activate on macOS/Linux
make install                      # or: pip install -e ".[openai,pdf]" pytest ruff mypy types-PyYAML
```

With `uv`: `uv sync` (uses `uv.lock`).

## Everyday commands

| Task | Command |
|---|---|
| Everything CI runs | `make check` |
| Tests | `make test` (`pytest -q`) |
| Lint / format | `make lint` / `make format` |
| Strict type check | `make typecheck` |
| Dependency vulnerabilities | `make audit` |
| SBOM | `make sbom` |
| Regenerate CLI reference and progress log | `make docs` |
| Clean-room check in a container | `docker compose run --rm check` |

## Conventions

- Strict `mypy`; `ruff` for lint and format (line length 100).
- Commands stay thin: no GitHub/AI/keyring logic inside `cli/commands/`.
- Output goes through `ConsoleRenderer`; never `print` in application code.
- Anything user-facing that could contain a secret goes through `SecretRedactor`.
- Mutating services: check permission, support `--dry-run`, confirm destructive steps, audit with `audit.record`, be idempotent.
- No global mutable state; dependencies are injected.

## Tests

`tests/unit/` uses fakes and `httpx.MockTransport`; no test contacts a real service. `conftest.py` provides `cli_sandbox(*roles)`: isolated config, an in-memory keyring, a temporary audit log and a signed-in identity with the given roles. Tests must not depend on a real `gh`, keyring or network (set `PATH=""` to hide `gh`).

## Project files written at runtime

`.stratos/` (agents, agent logs, backups, `skills.lock.json`), `.mcp.json`, `.claude/`, `CLAUDE.md`. All are edited conservatively (merge, managed blocks, backups) and can be rolled back.

## Releasing

Bump the version in `pyproject.toml` and `stratos/__init__.py`, update `CHANGELOG.md`, tag `vX.Y.Z`. The `release` workflow verifies the tag, builds, generates an SBOM, signs provenance and creates the release.
