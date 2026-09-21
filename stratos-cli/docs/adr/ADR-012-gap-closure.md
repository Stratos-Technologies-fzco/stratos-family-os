# ADR-012: Closing the Layer A-C gaps

Status: Accepted (21 September 2026)

## Decisions
1. **Async client shares the sync client's policy.** Header building, backoff, rate-limit detection and error mapping are module-level functions used by both clients; `AsyncPlatformApiClient` is used by network-bound knowledge sources (parallel fetches). Simple commands stay synchronous.
2. **GitHub coverage** now includes branches, pull requests, issues, labels (create/update, never delete), Actions permissions, a standard merge policy and security features. Security features unavailable on a plan report "unavailable" instead of failing the whole configuration.
3. **Provider port gained `chat`** (one turn with optional tool calls). Anthropic, OpenAI and Azure OpenAI implement it; SDKs stay lazy and optional (`stratos-cli[openai]`).
4. **Agents are tool-using but bounded.** Tools: `knowledge.search` (context), `knowledge.get`, `files.read`, `files.list` (project-confined, secrets files and VCS/venv folders blocked, output redacted and capped) and MCP tools. Every call is re-checked against the agent's permissions; a step limit (`agents.max_steps`) stops runaway loops.
5. **Skills run as instructions** appended to the agent's system prompt (they are instruction bundles); they are never executed as code.
6. **MCP execution** uses a minimal stdio JSON-RPC client. Servers start only if installed, allowed by `mcp.allowed` and the agent holds `run_commands` (not allowed by default); they receive a minimal environment (never the caller's whole environment) and are always terminated.
7. **Remote knowledge** (GitHub, Confluence, SharePoint) is read live, never cached. Confluence and SharePoint were verified against mocked responses only; SharePoint needs an externally obtained Graph access token (no interactive Microsoft login is implemented).
8. **Claude subagents** are exported to `.claude/agents/` with a Stratos marker; files without the marker are never overwritten. Tool lists follow the agent's permissions; the baseline is read-only.
9. **Governance:** organisation policy (`policy.allow_public_repos`, `policy.allowed_ai_providers`, `policy.allowed_ai_models`) is enforced in services and audited as `denied`; `audit summary` provides usage analytics and alerts (many denials, high failure rate, noisy user) with configurable thresholds (`monitoring.*`).
10. **Supply chain (M27):** CI on Linux and Windows, dependency audit, dependency review, CodeQL, gitleaks, CycloneDX SBOM, and a release pipeline with signed build provenance (optional trusted PyPI publishing). GitHub only runs workflows at a repository root; these live at the project root.

## Consequences
- Workflow actions are pinned to major versions; pinning to commit SHAs is recommended once the repository is final.
- `LICENSE` is intentionally left for the company to choose.
