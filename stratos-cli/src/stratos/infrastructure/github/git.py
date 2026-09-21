"""`git clone` wrapper. Uses the user's own git credentials; no token is ever put in the URL."""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from stratos.domain.exceptions import DependencyError, StratosError, ValidationError
from stratos.utils.redaction import SecretRedactor
from stratos.utils.validation import validate_name


def clone_repository(
    org: str,
    name: str,
    destination: Path,
    *,
    host: str = "github.com",
    run: Callable[..., Any] = subprocess.run,
) -> Path:
    validate_name(org, "organisation"), validate_name(name, "repository name")
    if shutil.which("git") is None:
        raise DependencyError("git is not installed or not on PATH.", hint="Install git and retry.")
    if destination.exists() and any(destination.iterdir()):
        raise ValidationError(f"Destination '{destination}' already exists and is not empty.")
    result = run(
        ["git", "clone", f"https://{host}/{org}/{name}.git", str(destination)],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if result.returncode != 0:
        detail = SecretRedactor().redact_text((result.stderr or "").strip())[:300]
        raise StratosError(f"git clone failed: {detail}")
    return destination
