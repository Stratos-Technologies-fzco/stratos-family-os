# Troubleshooting

Start with `stratos doctor` (no network). Add `--debug` to any command for detail and tracebacks.

## Exit codes

| Code | Meaning | Typical fix |
|---|---|---|
| 2 | Invalid usage | Check `stratos <command> --help` |
| 3 | Authentication | `stratos login`; for GitHub set `GITHUB_TOKEN` or `gh auth login`; for AI set the provider's API key variable |
| 4 | Authorisation | You lack the role, or organisation policy blocks it (`stratos org policy`, `stratos whoami`) |
| 5 | Not found | Check the name/id (`... list`) |
| 6 | Validation | Fix the input; the message says which field |
| 7 | Network/API | Check connectivity, `stratos config get api.endpoint`; rate limits are retried automatically |
| 8 | Configuration | `stratos config list`; the message names the missing setting |
| 9 | Dependency | Install the missing tool or fix the failing `doctor` check |
| 10 | Cancelled | You answered no, or pressed Ctrl+C |

## Common problems

**"Sign-in is not configured."** Set `auth.issuer` and `auth.client_id` (`stratos config set ...`). The issuer must be `https://` (plain `http` only for `localhost`).

**"No secure credential store is available."** Stratos never stores credentials in plain text. Install/unlock an OS keyring (Windows Credential Manager, macOS Keychain, or Secret Service on Linux).

**"You do not have permission for 'x'."** Roles come from your identity provider (`roles` or `groups` claim). Ask an administrator for the role, or check `stratos whoami`.

**GitHub "Must have admin rights" / 403.** Your GitHub account lacks access to that organisation or repository. Stratos does not bypass GitHub permissions.

**"Skill ... Checksum mismatch."** The skill contents differ from the registry index; it was not installed. Re-publish the skill index or investigate tampering.

**"MCP server ... not permitted by organisation policy."** Add it to `mcp.allowed` in the organisation configuration.

**"Agent ... needs permissions that are not allowed here."** Widen `agents.allowed_permissions` (administrators only). Agents use read-only permissions by default.

**PDF or OpenAI errors about missing packages.** `pip install "stratos-cli[pdf]"` / `"stratos-cli[openai]"`.

**Confluence/SharePoint 401.** Confluence needs `CONFLUENCE_EMAIL` and `CONFLUENCE_API_TOKEN`; SharePoint needs `SHAREPOINT_TOKEN` (or `MS_GRAPH_TOKEN`), an OAuth access token with read access. These sources were tested against mocked responses, not a live tenant.

**Machine-readable output looks wrong.** Use `-o json`; long values are never wrapped.

## Where things are

Config: user file in the OS config directory, project file `./.stratos/config.yaml`. Audit log and cache: OS state/cache directories. Backups: `.stratos/backups/` next to the project.
