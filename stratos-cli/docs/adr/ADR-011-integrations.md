# ADR-011: Enterprise integrations (Layer C)

Status: Accepted (21 September 2026)

## Context
Layer C connects the CLI to GitHub, Claude Code, skills, MCP servers, AI providers, agents and knowledge. The platform backend does not exist yet, and several modules depend on operation safety (M24) and caching (M25) from a later layer.

## Decisions
1. **M24 and M25 built early, in minimal form.** `application/safety.py` (dry run, confirmation, idempotency keys), `infrastructure/filesystem/config_files.py` (backup, diff, merge, validate, rollback, managed Markdown blocks) and `filesystem/cache.py` (TTL cache that refuses to store secrets; a value containing a secret is used once and never stored).
2. **GitHub through the shared API client.** `GitHubService` reuses the M07 client, gaining TLS, retries and request IDs. GitHub's primary rate limit (403 with `X-RateLimit-Remaining: 0`) waits until `X-RateLimit-Reset` when that is within 60 seconds. Repository creation is idempotent (existence check, then create, then re-check on a validation race). Names are validated before reaching any URL path. Token: `GITHUB_TOKEN`/`GH_TOKEN`, else the GitHub CLI's credential store; Stratos never stores it.
3. **Application services own the rules.** Permission check, dry run, confirmation, audit and idempotency live in services; commands only parse options and render.
4. **Claude Code files are edited conservatively.** `.mcp.json` and `.claude/settings.json` are merged, `CLAUDE.md` is edited only inside a `stratos:begin/end` block, skills are copied under `.claude/skills/`. Every change is backed up to `.stratos/backups/` and can be rolled back.
5. **Skills are verified, never executed.** Checksum, `stratos` version compatibility and declared permissions are checked before anything is written; permissions need approval. Registry source is a local directory with `index.json` (adapter behind the `SkillRegistry` port).
6. **MCP secrets never enter shared configuration.** Sensitive environment keys must be `${VAR}` references; secret-looking arguments and non-stdio transports are rejected. Organisation policy is `mcp.allowed` (unset means unrestricted).
7. **AI behind a port.** `AIProvider` (async `ask`, `stream`, `list_models`); `AnthropicProvider` is the first implementation and its SDK loads only when a call is made. Provider retries are bounded (3). Streaming output is redacted line by line.
8. **Agents are conservative.** A run is one model call built from instructions, optional knowledge context and the input. Only the `knowledge.search` tool exists; unknown tools are rejected at creation. An agent's permissions must be a subset of `agents.allowed_permissions` (default: `knowledge_read`, `repo_read`). Skills and MCP servers named on an agent are recorded, not yet executed. Run logs are redacted.
9. **Knowledge reads documents at query time.** The Markdown provider never copies document content into a cache; `sync` stores only a manifest (path, size, modified time, title).
10. **Audit and permissions extended** beyond the spec's nine actions: `repo.configure`, `repo.archive`, `skill.remove`, `mcp.configure`, `agent.create` (same-named permissions were added to the proposed role matrix).

## Consequences
- Skill, MCP, agent and knowledge sources are local directories today; remote registries and other knowledge sources (GitHub, PDF, Confluence, SharePoint) plug in behind the same ports.
- Using AI, knowledge or agents requires being signed in (`org.read` at minimum).
