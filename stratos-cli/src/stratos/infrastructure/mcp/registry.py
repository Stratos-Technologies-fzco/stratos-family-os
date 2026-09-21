"""Local MCP registry: index.json with {"servers": [ {name, description, command, args, env,
transport, version} ]}. `env` values must be ${VAR} references for secrets."""

import json
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from stratos.domain.exceptions import ConfigurationError
from stratos.domain.models.extensions import McpServerSpec


class LocalMcpRegistry:
    def __init__(self, root: Path) -> None:
        self._path = root / "index.json"

    def list(self) -> list[McpServerSpec]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigurationError(f"MCP registry not found at {self._path}.") from exc
        except ValueError as exc:
            raise ConfigurationError(f"MCP registry {self._path} is not valid JSON.") from exc
        servers = data.get("servers") if isinstance(data, dict) else None
        if not isinstance(servers, list):
            raise ConfigurationError(f"MCP registry {self._path} needs a 'servers' list.")
        try:
            return [McpServerSpec.model_validate(s) for s in servers]
        except PydanticValidationError as exc:
            raise ConfigurationError(
                f"Invalid MCP registry entry: {exc.error_count()} error(s)."
            ) from exc

    def get(self, name: str) -> McpServerSpec | None:
        return next((s for s in self.list() if s.name == name), None)
