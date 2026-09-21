"""`stratos doctor`: environment checks. Pure logic over facts the CLI gathers."""

from dataclasses import dataclass
from typing import Literal

Level = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class Check:
    name: str
    level: Level
    detail: str


@dataclass(frozen=True)
class Facts:
    python: tuple[int, int]
    config_error: str | None
    keyring_backend: str | None  # None when the keyring cannot be queried
    auth_configured: bool
    signed_in: bool | None  # None when sign-in is not configured
    git_found: bool
    github_token_found: bool
    claude_level: str  # "pass" | "warning"
    claude_detail: str
    ai_provider: str
    ai_key_configured: bool
    skills_registry: bool
    mcp_registry: bool
    knowledge_sources: int
    # Layer D additions (None means "not gathered")
    uv_found: bool | None = None
    docker_found: bool | None = None
    project_manifest: str | None = None  # "valid" | "missing" | "invalid: <why>"
    credential_env_vars: tuple[str, ...] | None = None  # names only, never values
    permissions: tuple[str, ...] | None = None
    online: dict[str, tuple[bool, str]] | None = None  # label -> (reachable, detail)


def run_checks(f: Facts) -> list[Check]:
    checks: list[Check] = []

    def add(name: str, ok: bool, good: str, bad: str, *, severe: bool = False) -> None:
        level: Level = "pass" if ok else ("fail" if severe else "warn")
        checks.append(Check(name, level, good if ok else bad))

    add(
        "Python",
        f.python >= (3, 12),
        f"{f.python[0]}.{f.python[1]}",
        "Python 3.12 or newer is required",
        severe=True,
    )
    add(
        "Configuration",
        f.config_error is None,
        "loads correctly",
        f.config_error or "",
        severe=True,
    )
    unusable = f.keyring_backend is None or "fail" in f.keyring_backend.lower()
    add(
        "Secure credential store",
        not unusable,
        f.keyring_backend or "",
        "no usable OS keyring; sign-in cannot store credentials (never saved in plain text)",
    )
    add(
        "Sign-in configured",
        f.auth_configured,
        "issuer and client id set",
        "run `stratos config set auth.issuer` and `auth.client_id`",
    )
    if f.signed_in is not None:
        add("Signed in", f.signed_in, "session is valid", "run `stratos login`")
    add("git", f.git_found, "found", "git is not on PATH (needed for `repo clone`)")
    add(
        "GitHub credentials",
        f.github_token_found,
        "token available",
        "set GITHUB_TOKEN or run `gh auth login`",
    )
    claude_level: Level = "pass" if f.claude_level == "pass" else "warn"
    checks.append(Check("Claude Code", claude_level, f.claude_detail))
    add(
        "AI provider",
        f.ai_key_configured,
        f"{f.ai_provider}: API key set",
        f"{f.ai_provider}: API key not set",
    )
    add("Skills registry", f.skills_registry, "configured", "not configured (optional)")
    add("MCP registry", f.mcp_registry, "configured", "not configured (optional)")
    add(
        "Knowledge sources",
        f.knowledge_sources > 0,
        f"{f.knowledge_sources} configured",
        "none configured (optional)",
    )
    if f.uv_found is not None:
        add("uv", f.uv_found, "found", "not found (optional; recommended for Python projects)")
    if f.docker_found is not None:
        add("Docker", f.docker_found, "found", "not found (optional)")
    if f.project_manifest is not None:
        if f.project_manifest.startswith("invalid"):
            checks.append(Check("Project configuration", "fail", f.project_manifest))
        else:
            add(
                "Project configuration",
                f.project_manifest == "valid",
                ".stratos/project.yaml is valid",
                "not a Stratos project here (run `stratos init`)",
            )
    if f.credential_env_vars is not None:
        add(
            "Environment variables",
            bool(f.credential_env_vars),
            ", ".join(f.credential_env_vars) + " set",
            "no credential variables set (e.g. GITHUB_TOKEN, ANTHROPIC_API_KEY)",
        )
    if f.permissions is not None:
        add(
            "Permissions",
            bool(f.permissions),
            f"{len(f.permissions)}: " + ", ".join(f.permissions),
            "none",
        )
    for label, (reachable, detail) in (f.online or {}).items():
        add(label, reachable, detail, detail)
    return checks


def summarise(checks: list[Check]) -> tuple[int, int, int]:
    """(passed, warnings, failures)"""
    return (
        sum(c.level == "pass" for c in checks),
        sum(c.level == "warn" for c in checks),
        sum(c.level == "fail" for c in checks),
    )
