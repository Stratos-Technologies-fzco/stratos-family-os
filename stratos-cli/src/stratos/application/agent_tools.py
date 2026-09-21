"""Tools an agent may call. Each tool declares the AgentPermission it needs; the runner refuses
to run a tool the agent was not granted."""

import fnmatch
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stratos.application.knowledge_service import KnowledgeService
from stratos.domain.enums import AgentPermission
from stratos.domain.exceptions import ResourceNotFoundError, ValidationError
from stratos.domain.models.extensions import AgentTool, ToolSpec
from stratos.utils.redaction import SecretRedactor
from stratos.utils.validation import sanitize_text

MAX_TOOL_OUTPUT = 20_000
MAX_FILE_BYTES = 100_000
MAX_LIST_ENTRIES = 200
_DENIED_DIRS = {".git", ".stratos", ".venv", "venv", "node_modules", "__pycache__"}
_DENIED_FILES = (".env", ".env.*", "*.pem", "*.key", "*.pfx", "*.p12", "id_rsa*", "id_ed25519*",
                 "credentials*", ".mcp.json", "*.kdbx")  # fmt: skip

# Tools an agent can be defined with. `knowledge.search` adds context before the model runs;
# the others are callable by the model during the run.
KNOWN_TOOLS: dict[str, AgentTool] = {
    "knowledge.search": AgentTool(
        name="knowledge.search",
        description="Adds relevant organisational knowledge to the prompt.",
        permission=AgentPermission.KNOWLEDGE_READ,
    ),
    "knowledge.get": AgentTool(
        name="knowledge.get",
        description="Read a knowledge document by id.",
        permission=AgentPermission.KNOWLEDGE_READ,
    ),
    "files.list": AgentTool(
        name="files.list",
        description="List files in the project (read-only).",
        permission=AgentPermission.REPO_READ,
    ),
    "files.read": AgentTool(
        name="files.read",
        description="Read a text file in the project (read-only; secrets files are blocked).",
        permission=AgentPermission.REPO_READ,
    ),
}

Handler = Callable[[dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class RuntimeTool:
    spec: ToolSpec
    permission: AgentPermission
    handler: Handler


def model_safe_name(name: str) -> str:
    """Tool names sent to models allow only letters, digits, '_' and '-'."""
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in name)[:64]


def _is_denied(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if any(part in _DENIED_DIRS for part in rel.parts):
        return True
    return any(fnmatch.fnmatch(path.name.lower(), pattern) for pattern in _DENIED_FILES)


def resolve_inside(root: Path, relative: str) -> Path:
    """Resolve `relative` under `root`; escapes and secrets files are refused."""
    root = root.resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise ValidationError("That path is outside the project.")
    if target != root and _is_denied(target, root):
        raise ValidationError("Access to that path is not allowed.")
    return target


def build_file_tools(root: Path, redactor: SecretRedactor) -> list[RuntimeTool]:
    async def read(args: dict[str, Any]) -> str:
        path = resolve_inside(root, str(args.get("path", "")))
        if not path.is_file():
            raise ResourceNotFoundError(f"File '{args.get('path')}' was not found.")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValidationError(f"File is larger than {MAX_FILE_BYTES // 1000} KB.")
        return redactor.redact_text(
            sanitize_text(path.read_text(encoding="utf-8", errors="replace"))
        )

    async def list_files(args: dict[str, Any]) -> str:
        base = resolve_inside(root, str(args.get("path") or "."))
        if not base.is_dir():
            raise ResourceNotFoundError(f"Folder '{args.get('path')}' was not found.")
        resolved_root = root.resolve()
        entries: list[str] = []
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.is_symlink() or _is_denied(p, resolved_root):
                continue
            entries.append(p.relative_to(resolved_root).as_posix())
            if len(entries) >= MAX_LIST_ENTRIES:
                entries.append("... (truncated)")
                break
        return "\n".join(entries) or "(no files)"

    path_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    return [
        RuntimeTool(
            ToolSpec(
                name="files_read",
                description=KNOWN_TOOLS["files.read"].description,
                input_schema=path_schema,
            ),
            AgentPermission.REPO_READ,
            read,
        ),  # fmt: skip
        RuntimeTool(
            ToolSpec(
                name="files_list",
                description=KNOWN_TOOLS["files.list"].description,
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            ),
            AgentPermission.REPO_READ,
            list_files,
        ),
    ]


def build_knowledge_tool(knowledge: KnowledgeService) -> RuntimeTool:
    async def get(args: dict[str, Any]) -> str:
        doc = await knowledge.get(str(args.get("id", "")))
        return f"# {doc.title}\n\n{doc.content}"

    return RuntimeTool(
        ToolSpec(
            name="knowledge_get",
            description=KNOWN_TOOLS["knowledge.get"].description,
            input_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        ),
        AgentPermission.KNOWLEDGE_READ,
        get,
    )
