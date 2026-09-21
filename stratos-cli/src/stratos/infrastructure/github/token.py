"""GitHub token lookup: GITHUB_TOKEN / GH_TOKEN, else the GitHub CLI's own credential store."""

import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from stratos.domain.exceptions import AuthenticationError


class GithubTokenProvider:
    """Callable returning a GitHub token; resolved once and never logged or persisted by Stratos."""

    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        *,
        which: Callable[[str], str | None] = shutil.which,
        run: Callable[..., Any] = subprocess.run,
    ) -> None:
        self._environ = os.environ if environ is None else environ
        self._which = which
        self._run = run
        self._token: str | None = None

    def __call__(self) -> str:
        if self._token:
            return self._token
        for name in ("GITHUB_TOKEN", "GH_TOKEN"):
            if self._environ.get(name):
                self._token = self._environ[name]
                return self._token
        gh = self._which("gh")
        if gh:
            try:
                out = self._run(
                    [gh, "auth", "token"], capture_output=True, text=True, timeout=10, check=False
                )
                if out.returncode == 0 and out.stdout.strip():
                    self._token = out.stdout.strip()
                    return self._token
            except (OSError, subprocess.SubprocessError):
                pass
        raise AuthenticationError(
            "No GitHub token found.", hint="Set GITHUB_TOKEN, or sign in with `gh auth login`."
        )
