# Changelog

## [Unreleased]

### Added
- **Foundation (M01-M06):** command shell, output formats, layered configuration, exit codes, logging with secret redaction, domain core.
- **Connectivity and trust (M07-M10):** OIDC + PKCE sign-in with keyring storage, role-based access control, sync and async API clients, audit trail with usage analytics (`audit summary`) and monitoring alerts, organisation policy (`policy.*`).
- **Integrations (M11-M18):** GitHub repositories, branches, pull requests, issues, labels, Actions, merge policy and security configuration; organisation and teams; Claude Code (instructions, MCP, skills, subagents, knowledge); skills; MCP servers; AI providers (Anthropic, OpenAI, Azure OpenAI) with tool calling; tool-using agents (files, knowledge, skills, MCP servers) under least privilege; knowledge sources (Markdown, PDF, GitHub, Confluence, SharePoint).
- **Cross-cutting:** dry run and confirmation guard, configuration-file protection, cache, `doctor`/`version`/`status`, CI (tests, lint, types), security pipeline (dependency audit, dependency review, CodeQL, secret scan, SBOM), signed release pipeline, generated CLI reference.

- **Developer workflow (M19-M23):** `project create | list | get | update | delete | init` (resumable 14-step creation), `init`, `workspace create | list | get | connect | delete`, `environment list | create | get | delete`, `deploy dev | staging | production | status | rollback`; `doctor` gains uv, Docker, project configuration, environment variables, permissions and `--online` connectivity checks; `version` and `status` extended.
- GitHub adapter: file read/write, repository description, Environments and Deployments with statuses.

### Notes
- Remote knowledge sources (Confluence, SharePoint) and the OpenAI/Azure providers were verified against mocked responses only.
- The Platform API contract is not published; no platform endpoints are assumed.
- **Cross-cutting engineering (M24-M29):** `Transaction` rollback for multi-step operations; `stratos backup list | restore`; `stratos cache status | prune | clear`; lazy command loading (`--help` imports no command module); composition root split and enforced by architecture tests; integration and end-to-end suites; 90% coverage gate; uv-based CI with macOS and package verification; `make setup`, `scripts/verify_package.py`, `scripts/mock_services.py`; ADR-001..009 and ADR-014; implementation plan; PR template.
- **User manual:** `docs/Stratos_CLI_User_Manual.pdf` (source `docs/manual/user_manual.html`): installation, setup, command guide, configuration reference, safety features and a message-by-message troubleshooting guide.
- **npm installer:** `npm install -g @stratos-technologies-fzco/stratos-cli` installs the CLI (bundled wheel, private Python environment, `stratos` launcher). Build with `make npm-pack`. User manual gains section 3.7.
- **Publishing:** npm package renamed `@stratos-technologies-fzco/stratos-cli` with GitHub Packages `publishConfig`; `docs/PUBLISHING.md`; manual 3.7 documents registry setup.
