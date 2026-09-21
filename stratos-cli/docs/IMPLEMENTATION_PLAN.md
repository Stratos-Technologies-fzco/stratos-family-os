# Implementation plan

| Layer | Modules | Status |
|---|---|---|
| A Foundation | M01 Package/entry, M02 Errors/exit codes, M03 Configuration, M04 Logging, M05 Output rendering, M06 Utilities | Done |
| B Connectivity & Trust | M07 Authentication (OIDC+PKCE), M08 Platform API client, M09 Authorization, M10 Audit | Done |
| C Enterprise Integration | M11 GitHub, M12 Repositories, M13 Claude Code, M14 Skills, M15 MCP, M16 AI, M17 Knowledge, M18 Agents | Done |
| D Developer Workflow | M19 Projects, M20 init, M21 Workspaces, M22 Environments/Deployments, M23 Diagnostics | Done |
| E Cross-cutting | M24 Operation safety, M25 Caching/performance, M26 Testing, M27 Build/CI/CD, M28 Docs/ADRs, M29 Local dev | Done (this document) |

## Outstanding dependencies (not buildable yet)
- **Platform API.** `docs/openapi.yaml` is an empty placeholder until the contract is designed. Until it exists, local adapters and GitHub stand in (ADR-009). Switching is a change of adapter, not of commands.
- **Company LICENSE** and organisation membership for a dedicated repository.
- **Release signing identity** for `release.yml`.

## Definition of done (also in the PR template)
Lint, format and strict mypy pass; tests added; coverage stays at or above 90%; docs and CLI reference regenerated; progress log updated; no secrets in code, logs or fixtures.
