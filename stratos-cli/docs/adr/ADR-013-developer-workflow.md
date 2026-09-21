# ADR-013: Developer workflow (Layer D)

Status: Accepted (21 September 2026)

## Context
Layer D (projects, `init`, workspaces, environments, deployments, diagnostics) describes platform concepts, but the Platform API does not exist. The specification says to use adapters instead of inventing a backend.

## Decisions
1. **GitHub is the backend where GitHub already has the concept.** Repositories, branch protection, CODEOWNERS, labels and CI files (existing); **GitHub Environments** for environments; **GitHub Deployments** (+ statuses) for deployments. Stratos requests a deployment and posts `queued`; the project's CI performs the rollout and posts the real statuses. Stratos never reports a deployment as successful on its own.
2. **Local registry adapters** (`ProjectStore`, `WorkspaceStore`, JSON in the user data directory) hold project and workspace records until the Platform API can. They write atomically and refuse to overwrite a damaged file.
3. **Project creation is a resumable step workflow.** 14 named steps; the record stores completed steps, so a failure leaves a `partial` project that the same command resumes. Every step is also individually idempotent. Nothing is deleted automatically on failure. One `project.create` audit event wraps the workflow (success, failure or denied).
4. **Claude configuration for a new project is committed to the repository** (`CLAUDE.md` managed blocks, `.claude/skills/`, `.mcp.json`, `.stratos/project.yaml`). Skills are checksum-verified and MCP servers validated and policy-checked before anything is committed; developer content in existing files is preserved via managed blocks and JSON merging.
5. **`init` reuses the same building blocks locally** and is transactional: all writes are backed up, diffs are shown, and a failure rolls everything back. Dry-run uses a protector that computes results without writing, so preview and real run share one code path. It also adds `.env` deny rules to `.claude/settings.json` (existing rules kept).
6. **Workspaces never run third-party code by default.** Dependency installation is opt-in (`--install-deps`), Node installs use `--ignore-scripts`, `.env` and existing git hooks are never overwritten, and the installed pre-commit hook blocks `.env` files and obvious credentials. Purging a folder refuses uncommitted work unless `--force`, and refuses anything that is not a git working copy.
7. **Confirmation and retries.** Production deploys, rollbacks and environment/project/workspace deletion confirm first. Deployments are created without an idempotency key so they are never retried after a failure; an identical in-flight deployment is detected and returned instead of duplicated. Rollback redeploys the previous *successful* version (never a failed one) as a new deployment.
8. **Diagnostics:** `doctor` gains uv, Docker, project configuration, credential variable names (never values) and permissions; `--online` adds network, GitHub API and Platform API probes (no credentials sent; unreachable services are warnings). `version` adds architecture and API version; `status` summarises project, workspaces and environments locally.
9. Additional permissions and audit actions (beyond the specification's list): `project.update`, `project.init`, `workspace.create/delete`, `environment.create/delete`.

## Consequences
- Environments and deployments require a GitHub-hosted project and sufficient GitHub rights (protection rules may need a paid plan; creation falls back to an unprotected environment).
- When the Platform API arrives, `ProjectStore`, `WorkspaceStore` and the deployment port get new adapters without changing services or commands.
