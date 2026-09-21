"""Gather the local facts behind `stratos doctor` (M23). Network probes only when asked."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from stratos.config.loader import load_settings
from stratos.config.settings import Settings
from stratos.domain.exceptions import ConfigurationError

if TYPE_CHECKING:
    from stratos.application.diagnostics import Facts
    from stratos.cli.context import CliContext

CREDENTIAL_VARIABLES = (
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "CONFLUENCE_API_TOKEN",
    "SHAREPOINT_TOKEN",
)


def _settings_or_defaults(ctx: "CliContext") -> tuple[Settings, str | None]:
    try:
        return ctx.settings, None
    except ConfigurationError as exc:
        defaults = load_settings(
            {},
            env={},
            org_path=Path(os.devnull),
            user_path=Path(os.devnull),
            project_path=Path(os.devnull),
        )
        return defaults, exc.message


def _keyring_backend() -> str | None:
    try:
        import keyring

        backend = keyring.get_keyring()
        return f"{type(backend).__module__.split('.')[-2:][0]}.{type(backend).__name__}"
    except Exception:  # noqa: BLE001 - any failure means "not usable"
        return None


def _github_token_available() -> bool:
    if os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"):
        return True
    if not shutil.which("gh"):
        return False
    try:
        out = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def _manifest_state(ctx: "CliContext") -> str:
    from stratos.application.init_service import read_manifest

    try:
        return "valid" if read_manifest(ctx.project_dir) else "missing"
    except ConfigurationError as exc:
        return f"invalid: {exc.message}"


def _permissions(ctx: "CliContext") -> tuple[str, ...] | None:
    try:
        granted = ctx.authorization.permissions_for(ctx.auth_service.whoami())
        return tuple(sorted(p.value for p in granted))
    except Exception:  # noqa: BLE001
        return None


def _online_probes(settings: Settings) -> dict[str, tuple[bool, str]]:
    from stratos.infrastructure.api.probes import http_probe, tcp_probe

    return {
        "Network (github.com)": tcp_probe("github.com", 443),
        "GitHub API": http_probe(settings.github.api_url.rstrip("/") + "/rate_limit"),
        "Platform API": http_probe(str(settings.api.endpoint), any_response=True),
    }


def _knowledge_source_count(settings: Settings) -> int:
    k = settings.knowledge
    count = len(k.paths) + len(k.pdf_paths) + len(k.github) + len(k.sharepoint_sites)
    return count + (1 if k.confluence_url else 0)


def gather_facts(ctx: "CliContext", *, online: bool) -> "Facts":
    from stratos.application.diagnostics import Facts
    from stratos.infrastructure.ai import api_key_env_for

    settings, config_error = _settings_or_defaults(ctx)
    auth_configured = bool(settings.auth.issuer and settings.auth.client_id)
    signed_in: bool | None = None
    if auth_configured:
        try:
            signed_in = ctx.auth_service.status().authenticated
        except Exception:  # noqa: BLE001
            signed_in = False
    claude_level, claude_detail = ctx.claude.detect().doctor_check()
    return Facts(
        python=(sys.version_info.major, sys.version_info.minor),
        config_error=config_error,
        keyring_backend=_keyring_backend(),
        auth_configured=auth_configured,
        signed_in=signed_in,
        git_found=shutil.which("git") is not None,
        github_token_found=_github_token_available(),
        claude_level=claude_level,
        claude_detail=claude_detail,
        ai_provider=settings.ai.provider,
        ai_key_configured=bool(os.environ.get(api_key_env_for(settings.ai.provider))),
        skills_registry=bool(settings.skills.registry),
        mcp_registry=bool(settings.mcp.registry),
        knowledge_sources=_knowledge_source_count(settings),
        uv_found=shutil.which("uv") is not None,
        docker_found=shutil.which("docker") is not None,
        project_manifest=_manifest_state(ctx),
        credential_env_vars=tuple(n for n in CREDENTIAL_VARIABLES if os.environ.get(n)),
        permissions=_permissions(ctx) if signed_in else None,
        online=_online_probes(settings) if online else None,
    )
