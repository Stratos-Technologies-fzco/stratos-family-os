"""Per-invocation command context (dependency injection).

Settings load lazily so `--help` never touches configuration or any client.
"""

from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from stratos.cli.output import ConsoleRenderer
from stratos.config.loader import load_settings
from stratos.config.settings import Settings
from stratos.utils.redaction import SecretRedactor


@dataclass
class CliContext:
    renderer: ConsoleRenderer
    redactor: SecretRedactor = field(default_factory=SecretRedactor)
    verbose: bool = False
    debug: bool = False
    overrides: dict[str, Any] = field(default_factory=dict)

    @cached_property
    def settings(self) -> Settings:
        return load_settings(self.overrides)
