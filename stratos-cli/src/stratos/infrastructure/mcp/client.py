"""Minimal MCP client over stdio (newline-delimited JSON-RPC 2.0).

Supports what agents need: initialize, tools/list and tools/call. The server process gets a
minimal environment (never the caller's full environment), and every wait is time-limited.
"""

import asyncio
import json
import os
import shutil
from asyncio.subprocess import Process
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from stratos import __version__
from stratos.domain.exceptions import APIError, DependencyError
from stratos.domain.models.extensions import McpToolInfo
from stratos.utils.redaction import SecretRedactor, is_env_reference

PROTOCOL_VERSION = "2024-11-05"
_BASE_ENV = ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TEMP", "TMP", "LANG", "PATHEXT")
_redactor = SecretRedactor()


def build_server_env(config_env: Mapping[str, str], environ: Mapping[str, str]) -> dict[str, str]:
    """Minimal environment plus the variables the server config declares.
    `${VAR}` references are resolved from `environ`; unset references are omitted."""
    env = {k: environ[k] for k in _BASE_ENV if k in environ}
    for key, value in config_env.items():
        if is_env_reference(value):
            resolved = environ.get(value[2:-1])
            if resolved is not None:
                env[key] = resolved
        else:
            env[key] = value
    return env


class McpStdioClient:
    def __init__(
        self,
        command: str,
        args: list[str],
        env: Mapping[str, str],
        *,
        timeout: float = 30.0,
        which: Callable[[str], str | None] = shutil.which,
        spawn: Callable[..., Awaitable[Process]] = asyncio.create_subprocess_exec,
    ) -> None:
        self._command = command
        self._args = args
        self._env = dict(env)
        self._timeout = timeout
        self._which = which
        self._spawn = spawn
        self._proc: Process | None = None
        self._next_id = 0

    async def __aenter__(self) -> "McpStdioClient":
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def start(self) -> None:
        exe = self._which(self._command) or (
            self._command if Path(self._command).is_file() else None
        )
        if exe is None:
            raise DependencyError(f"MCP server program '{self._command}' was not found.")
        self._proc = await self._spawn(
            exe,
            *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=self._env,
            limit=1 << 20,
        )
        await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "stratos", "version": __version__},
            },
        )
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.stdin:
            proc.stdin.close()
        if proc.returncode is None:
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except TimeoutError:
            proc.kill()
            await proc.wait()

    async def list_tools(self) -> list[McpToolInfo]:
        result = await self._request("tools/list", {})
        return [
            McpToolInfo(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema") or {"type": "object", "properties": {}},
            )
            for t in result.get("tools", [])
        ]

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> tuple[str, bool]:
        """Returns (text, is_error)."""
        result = await self._request("tools/call", {"name": name, "arguments": dict(arguments)})
        parts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        return "\n".join(parts), bool(result.get("isError"))

    # ---- JSON-RPC plumbing ---------------------------------------------------------------
    async def _send(self, message: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise APIError("The MCP server is not running.")
        self._proc.stdin.write((json.dumps(message) + "\n").encode())
        await self._proc.stdin.drain()

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            return await asyncio.wait_for(self._await_response(request_id), self._timeout)
        except TimeoutError as exc:
            raise APIError(f"The MCP server did not answer '{method}' in time.") from exc

    async def _await_response(self, request_id: int) -> dict[str, Any]:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                raise APIError("The MCP server closed the connection.")
            try:
                message = json.loads(line)
            except ValueError:
                continue  # stray output that is not JSON-RPC
            if not isinstance(message, dict):
                continue
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    detail = str((message["error"] or {}).get("message", "unknown error"))
                    raise APIError(f"MCP server error: {_redactor.redact_text(detail)[:200]}")
                result = message.get("result")
                return result if isinstance(result, dict) else {}
            if "method" in message and "id" in message:  # server asks us something: decline
                await self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32601, "message": "Method not supported by this client"},
                    }
                )


def default_environ() -> Mapping[str, str]:
    return os.environ
