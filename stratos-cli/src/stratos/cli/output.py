"""Central rendering layer. All user-facing output goes through here."""

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm
from rich.syntax import Syntax
from rich.table import Table

from stratos.domain.enums import OutputFormat
from stratos.utils.redaction import SecretRedactor
from stratos.utils.validation import sanitize, sanitize_text

# Semantic palette: success green, warning orange, error red, info blue, AI purple, security cyan.
STYLES = {
    "success": "green",
    "warning": "dark_orange",
    "error": "red",
    "info": "blue",
    "ai": "medium_purple",
    "security": "cyan",
}


class ConsoleRenderer:
    def __init__(
        self,
        fmt: OutputFormat = OutputFormat.TABLE,
        *,
        redactor: SecretRedactor | None = None,
        console: Console | None = None,
        err_console: Console | None = None,
    ) -> None:
        self.format = fmt
        self._redactor = redactor or SecretRedactor()
        self._out = console or Console()
        self._err = err_console or Console(stderr=True)

    # ---- data ----------------------------------------------------------
    def data(
        self,
        rows: Sequence[Mapping[str, Any]] | Mapping[str, Any],
        *,
        title: str | None = None,
    ) -> None:
        clean = sanitize(self._redactor.redact(rows if isinstance(rows, Mapping) else list(rows)))
        if self.format is OutputFormat.QUIET:
            return
        if self.format is OutputFormat.JSON:
            self._out.print(
                Syntax(json.dumps(clean, indent=2, default=str), "json", background_color="default")
                if self._out.is_terminal
                else json.dumps(clean, indent=2, default=str),
                markup=False,
                highlight=False,
            )
        elif self.format is OutputFormat.YAML:
            self._out.print(
                yaml.safe_dump(clean, sort_keys=False, default_flow_style=False).rstrip(),
                markup=False,
                highlight=False,
            )
        elif self.format is OutputFormat.PLAIN:
            for row in [clean] if isinstance(clean, dict) else clean:
                self._out.print(
                    "\t".join(str(v) for v in row.values()), markup=False, highlight=False
                )
        else:
            self._out.print(self._table(clean, title))

    @staticmethod
    def _table(clean: Any, title: str | None) -> Table:
        records: list[dict[str, Any]]
        if isinstance(clean, dict):
            table = Table(title=title, show_header=True, header_style="bold")
            table.add_column("Key")
            table.add_column("Value")
            for key, value in clean.items():
                table.add_row(str(key), str(value))
            return table
        records = clean
        table = Table(title=title, show_header=True, header_style="bold")
        columns = list(records[0]) if records else []
        for col in columns:
            table.add_column(str(col))
        for record in records:
            table.add_row(*(str(record.get(c, "")) for c in columns))
        return table

    # ---- messages ------------------------------------------------------
    def _message(self, style: str, symbol: str, message: str, *, err: bool = False) -> None:
        if self.format is OutputFormat.QUIET and not err:
            return
        text = sanitize_text(self._redactor.redact_text(message))
        (self._err if err else self._out).print(f"[{STYLES[style]}]{symbol}[/] ", end="")
        (self._err if err else self._out).print(text, markup=False, highlight=False)

    def success(self, message: str) -> None:
        self._message("success", "✔", message)

    def info(self, message: str) -> None:
        self._message("info", "ℹ", message)

    def warning(self, message: str) -> None:
        self._message("warning", "!", message, err=True)

    def error(self, message: str, *, hint: str | None = None) -> None:
        self._message("error", "✖", message, err=True)
        if hint:
            self._err.print(
                f"  Hint: {self._redactor.redact_text(hint)}", markup=False, highlight=False
            )

    def debug(self, message: str) -> None:
        """Diagnostic detail (e.g. tracebacks) on stderr; only used under --debug."""
        self._err.print(self._redactor.redact_text(message), markup=False, highlight=False)

    def secret(self, value: str) -> None:
        """Print a secret verbatim. Bypasses redaction; only for explicit opt-in commands."""
        self._out.print(value, markup=False, highlight=False, soft_wrap=True)

    def panel(self, body: str, *, title: str | None = None, style: str = "info") -> None:
        if self.format is not OutputFormat.QUIET:
            self._out.print(
                Panel(self._redactor.redact_text(body), title=title, border_style=STYLES[style])
            )

    # ---- interaction ---------------------------------------------------
    @contextmanager
    def status(self, message: str) -> Iterator[None]:
        """Spinner for long operations (suppressed for machine-readable formats)."""
        if self.format in (OutputFormat.TABLE, OutputFormat.PLAIN) and self._err.is_terminal:
            with Progress(
                SpinnerColumn(), TextColumn("{task.description}"), console=self._err, transient=True
            ) as progress:
                progress.add_task(message, total=None)
                yield
        else:
            yield

    def confirm(self, message: str, *, default: bool = False) -> bool:
        return Confirm.ask(message, default=default, console=self._err)
