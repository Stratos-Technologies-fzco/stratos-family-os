"""MCP server management under organisation policy, with protected secrets and rollback."""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from stratos.application.audit_service import AuditService
from stratos.application.safety import OperationGuard
from stratos.domain.enums import AuditAction, AuditResult, Permission
from stratos.domain.exceptions import (
    AuthorizationError,
    ResourceNotFoundError,
    StratosError,
    ValidationError,
)
from stratos.domain.interfaces import Cache, ClaudeProject, McpRegistry
from stratos.domain.models.auth import Identity
from stratos.domain.models.extensions import McpServerSpec
from stratos.domain.models.files import WriteResult
from stratos.utils.redaction import SecretRedactor, is_sensitive_key

Require = Callable[[Permission], Identity]
_ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COMMAND = re.compile(r"^[A-Za-z0-9_./\\:@+-]+$")
CACHE_TTL = 300.0


def validate_server(spec: McpServerSpec, redactor: SecretRedactor | None = None) -> None:
    """Reject anything that could put a secret or an unsafe command into shared configuration."""
    redactor = redactor or SecretRedactor()
    if spec.transport != "stdio":
        raise ValidationError(
            f"MCP server '{spec.name}': transport '{spec.transport}' is not supported yet."
        )
    if not _COMMAND.match(spec.command):
        raise ValidationError(
            f"MCP server '{spec.name}': the command must be a plain program name or path."
        )
    for arg in spec.args:
        if redactor.redact_text(arg) != arg:
            raise ValidationError(
                f"MCP server '{spec.name}': an argument looks like a secret.",
                hint="Pass secrets through environment variables, e.g. ${API_KEY}.",
            )
    for key, value in spec.env.items():
        if _ENV_REF.match(value):
            continue
        if is_sensitive_key(key) or redactor.redact_text(f"{key}={value}") != f"{key}={value}":
            raise ValidationError(
                f"MCP server '{spec.name}': {key} must reference an environment variable "
                "(${NAME}), not contain a value.",
                hint="Secrets are never written to shared configuration.",
            )


def to_config(spec: McpServerSpec) -> dict[str, Any]:
    config: dict[str, Any] = {"command": spec.command}
    if spec.args:
        config["args"] = list(spec.args)
    if spec.env:
        config["env"] = dict(spec.env)
    return config


@dataclass(frozen=True)
class McpRow:
    name: str
    description: str
    installed: bool
    allowed: bool


@dataclass(frozen=True)
class McpStatusRow:
    name: str
    in_registry: bool
    allowed: bool
    missing_env: tuple[str, ...]


class McpService:
    def __init__(
        self,
        registry: McpRegistry,
        claude: ClaudeProject,
        require: Require,
        audit: AuditService,
        guard: OperationGuard,
        cache: Cache,
        *,
        allowed: list[str] | None,
        environ: Mapping[str, str],
        registry_id: str,
    ) -> None:
        self._registry = registry
        self._claude = claude
        self._require = require
        self._audit = audit
        self._guard = guard
        self._cache = cache
        self._allowed = allowed
        self._environ = environ
        self._registry_id = registry_id

    def _is_allowed(self, name: str) -> bool:
        return self._allowed is None or name in self._allowed

    def _specs(self) -> list[McpServerSpec]:
        data = self._cache.get_or_load(
            "mcp-registry",
            self._registry_id,
            CACHE_TTL,
            lambda: [s.model_dump(mode="json") for s in self._registry.list()],
        )
        return [McpServerSpec.model_validate(d) for d in data]

    # ---- list / status -------------------------------------------------------------------
    def list_servers(self) -> list[McpRow]:
        self._require(Permission.ORG_READ)
        installed = self._claude.mcp_servers()
        return [
            McpRow(s.name, s.description, s.name in installed, self._is_allowed(s.name))
            for s in self._specs()
        ]

    def status(self) -> list[McpStatusRow]:
        self._require(Permission.ORG_READ)
        known = {s.name for s in self._specs()}
        rows = []
        for name, config in sorted(self._claude.mcp_servers().items()):
            refs = [
                m.group(1)
                for v in (config.get("env") or {}).values()
                if (m := _ENV_REF.match(str(v)))
            ]
            rows.append(
                McpStatusRow(
                    name,
                    name in known,
                    self._is_allowed(name),
                    tuple(sorted(r for r in refs if r not in self._environ)),
                )
            )
        return rows

    # ---- checks are audited (denied / failure) but never write anything ------------------
    def _checked(self, action: AuditAction, name: str, check: Callable[[], None]) -> None:
        try:
            check()
        except AuthorizationError:
            self._audit.emit(action, "mcp", name, result=AuditResult.DENIED)
            raise
        except StratosError:
            self._audit.emit(action, "mcp", name, result=AuditResult.FAILURE)
            raise

    # ---- install -------------------------------------------------------------------------
    def install(self, name: str) -> WriteResult | None:
        """Install one server into .mcp.json. Returns None in dry-run mode."""
        self._require(Permission.MCP_INSTALL)
        specs = {s.name: s for s in self._specs()}

        def check() -> None:
            if name not in specs:
                raise ResourceNotFoundError(f"MCP server '{name}' is not in the registry.")
            if not self._is_allowed(name):
                raise AuthorizationError(
                    f"MCP server '{name}' is not permitted by organisation policy.",
                    hint="Ask an administrator to add it to mcp.allowed.",
                )
            validate_server(specs[name])

        self._checked(AuditAction.MCP_INSTALL, name, check)
        if self._guard.preview(
            "mcp install", [f"Add '{name}' to .mcp.json (existing file is backed up)"]
        ):
            return None
        with self._audit.record(AuditAction.MCP_INSTALL, "mcp", name):
            return self._claude.merge_mcp_servers({name: to_config(specs[name])})

    # ---- configure -----------------------------------------------------------------------
    def configure(
        self, name: str, *, env: Mapping[str, str] | None = None, args: list[str] | None = None
    ) -> WriteResult | None:
        """Change an installed server: map env keys to variable names and/or set its args."""
        self._require(Permission.MCP_CONFIGURE)
        current = self._claude.mcp_servers().get(name)
        if current is None:
            raise ResourceNotFoundError(f"MCP server '{name}' is not installed.")
        merged: dict[str, Any] = {}

        def check() -> None:
            env_refs: dict[str, str] = {}
            for key, var in (env or {}).items():
                ref = _ENV_REF.match(var)
                var_name = ref.group(1) if ref else var
                if not _ENV_NAME.match(var_name):
                    raise ValidationError(
                        f"'{var[:30]}' is not an environment variable name for {key}."
                    )
                env_refs[key] = "${" + var_name + "}"
            merged.update(current)
            merged["env"] = {**(current.get("env") or {}), **env_refs}
            if args is not None:
                merged["args"] = list(args)
            validate_server(
                McpServerSpec(
                    name=name,
                    command=merged["command"],
                    args=tuple(merged.get("args", ())),
                    env=merged["env"],
                )
            )

        self._checked(AuditAction.MCP_CONFIGURE, name, check)
        if self._guard.preview(
            "mcp configure", [f"Update '{name}' in .mcp.json (existing file is backed up)"]
        ):
            return None
        with self._audit.record(AuditAction.MCP_CONFIGURE, "mcp", name):
            return self._claude.merge_mcp_servers({name: merged})

    def rollback(self, result: WriteResult) -> None:
        self._claude.rollback(result)
