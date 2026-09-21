# Contributing

1. Create a branch from `main`.
2. Make the change with tests. Run `make check` (lint, strict types, tests) until it passes.
3. If behaviour, commands or security posture change, update the relevant docs (`make docs` refreshes the CLI reference) and add an entry to `CHANGELOG.md`. Significant design choices get an ADR in `docs/adr/`.
4. Open a pull request. CI must pass (tests on Linux and Windows, lint, types, dependency audit, CodeQL, secret scan).

## Ground rules

- **Never commit secrets.** Use environment variables and the OS keyring. `.env` is git-ignored; `.env.example` lists names only.
- **Do not invent backend behaviour.** Until the Platform API exists, use adapters and mocks and say so.
- **Least privilege by default.** New permissions, agent tools and integrations must be opt-in and audited.
- **Keep commands thin** and put rules in application services (see `docs/ARCHITECTURE.md`).
- **Small, reviewable changes.** Prefer several focused pull requests.

## Reporting a security issue

Do not open a public issue. Contact the security team privately (see `docs/SECURITY.md`).
