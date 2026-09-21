"""Path safety helpers."""

from pathlib import PurePosixPath

from stratos.domain.exceptions import ValidationError


def safe_relative(rel: str) -> PurePosixPath:
    """A relative POSIX path that cannot escape its folder (no absolute paths, '..' or drives)."""
    path = PurePosixPath(rel.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts or ":" in path.parts[0]:
        raise ValidationError(f"Unsafe file path in skill: '{rel[:80]}'.")
    return path
