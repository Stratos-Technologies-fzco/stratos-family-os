"""Credential storage. Only the OS keyring is used; there is deliberately no file fallback."""

from typing import Any

from stratos.domain.exceptions import DependencyError

_CHUNK = 900  # Windows Credential Manager rejects values above ~2.5 KB; JWTs can exceed that.


class KeyringSecretStore:
    """Stores secrets in the operating system keyring (chunked for large values)."""

    def __init__(self, service: str = "stratos", backend: Any = None) -> None:
        if backend is None:
            import keyring  # imported lazily to keep `stratos --help` fast

            backend = keyring
        self._kr = backend
        self._service = service

    def _call(self, fn: str, *args: str) -> Any:
        try:
            return getattr(self._kr, fn)(self._service, *args)
        except Exception as exc:  # keyring raises backend-specific errors
            if type(exc).__name__ == "PasswordDeleteError":
                return None
            raise DependencyError(
                "No secure credential store is available on this machine.",
                hint="Credentials are never stored in plain text. Install or unlock an OS keyring.",
            ) from exc

    def get(self, key: str) -> str | None:
        count = self._call("get_password", f"{key}#n")
        if count is None:
            return None
        parts = [self._call("get_password", f"{key}#{i}") for i in range(int(count))]
        return None if any(p is None for p in parts) else "".join(parts)

    def set(self, key: str, value: str) -> None:
        self.delete(key)
        chunks = [value[i : i + _CHUNK] for i in range(0, len(value), _CHUNK)] or [""]
        for i, chunk in enumerate(chunks):
            self._call("set_password", f"{key}#{i}", chunk)
        self._call("set_password", f"{key}#n", str(len(chunks)))

    def delete(self, key: str) -> None:
        count = self._call("get_password", f"{key}#n")
        if count is None:
            return
        for i in range(int(count)):
            self._call("delete_password", f"{key}#{i}")
        self._call("delete_password", f"{key}#n")


class InMemorySecretStore:
    """Test double / ephemeral store. Never persists."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)
