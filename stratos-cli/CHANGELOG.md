# Changelog

## [Unreleased]

### Added
- **Foundation (M01-M06):** command shell, output formats, layered configuration, exit codes, logging with secret redaction, domain core.
- **Connectivity and trust (M07-M10):** OIDC + PKCE sign-in with keyring storage, role-based access control, sync and async API clients, audit trail with usage analytics (`audit summary`) and monitoring alerts, organisation policy (`policy.*`).
- **Integrations (M11-M18):** GitHub repositories, branches, pull requests, issues, labels, Actions, merge policy and security configuration; organisation and teams; Claude Code (instructions, MCP, skills, subagents, knowledge); skills; MCP servers; AI providers (Anthropic, OpenAI, Azure OpenAI) with tool calling; tool-using agents (files, knowledge, skills, MCP servers) under least privilege; knowledge sources (Markdown, PDF, GitHub, Confluence, SharePoint).
- **Cross-cutting:** dry run and confirmation guard, configuration-file protection, cache, `doctor`/`version`/`status`, CI (tests, lint, types), security pipeline (dependency audit, dependency review, CodeQL, secret scan, SBOM), signed release pipeline, generated CLI reference.

### Notes
- Remote knowledge sources (Confluence, SharePoint) and the OpenAI/Azure providers were verified against mocked responses only.
- The Platform API contract is not published; no platform endpoints are assumed.
