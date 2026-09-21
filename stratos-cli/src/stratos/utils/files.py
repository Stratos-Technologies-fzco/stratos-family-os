"""Bounded, safe file reading for command inputs."""

from pathlib import Path

from stratos.domain.exceptions import ResourceNotFoundError, ValidationError
from stratos.utils.validation import sanitize_text


def read_text_limited(path: Path, max_bytes: int = 200_000) -> str:
    if not path.is_file():
        raise ResourceNotFoundError(f"File '{path}' was not found.")
    if path.stat().st_size > max_bytes:
        raise ValidationError(f"File '{path.name}' is larger than {max_bytes // 1000} KB.")
    return sanitize_text(path.read_text(encoding="utf-8", errors="replace"))
