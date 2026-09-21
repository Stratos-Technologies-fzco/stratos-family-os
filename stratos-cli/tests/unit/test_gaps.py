"""Tests for the gap-closing work: async client, GitHub extras, OpenAI/Azure, MCP client, agent
tool loop, remote knowledge sources, Claude subagents, diagnostics, governance and CI files."""

import base64
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import yaml
from test_layer_c import (
    BASE,
    NOW,
    REPO,
    FakeGitHub,
    FakeProvider,
    audit_results,
    gh,
    make_audit,
    make_docs,
    make_guard,
    make_provider,
    make_repo_service,
    make_require,
    run,
)
from typer.testing import CliRunner

from stratos.application.agent_runner import AgentRunner
from stratos.application.agent_service import AgentService
from stratos.application.agent_tools import resolve_inside
from stratos.application.ai_service import AIService
from stratos.application.claude_service import ClaudeIntegrationService
from stratos.application.diagnostics import Facts, run_checks, summarise
from stratos.application.knowledge_service import KnowledgeService
from stratos.application.policy_service import PolicyService
from stratos.cli.app import app
from stratos.config.loader import load_settings
from stratos.domain.enums import AgentPermission, AuditAction, AuditResult
from stratos.domain.exceptions import (
    APIError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    DependencyError,
    NetworkError,
    ResourceNotFoundError,
    ValidationError,
)
from stratos.domain.models.extensions import (
    AgentDefinition,
    ChatMessage,
    ChatTurn,
    ToolCall,
)
from stratos.domain.models.github import Label
from stratos.infrastructure.ai import api_key_env_for, create_provider
from stratos.infrastructure.ai.anthropic import AnthropicProvider, to_anthropic_messages
from stratos.infrastructure.ai.openai_compat import (
    AzureOpenAIProvider,
    OpenAIProvider,
    to_openai_messages,
)
from stratos.infrastructure.api.async_client import AsyncPlatformApiClient
from stratos.infrastructure.api.mock import mock_transport
from stratos.infrastructure.claude.manager import AGENT_MARKER, ClaudeCodeManager
from stratos.infrastructure.filesystem.agent_registry import (
    BUILTIN_AGENTS,
    FilesystemAgentRegistry,
    FilesystemAgentRunLog,
)
from stratos.infrastructure.filesystem.audit_log import LocalAuditStore
from stratos.infrastructure.knowledge import remote
from stratos.infrastructure.knowledge.pdf import PdfKnowledgeProvider, extract_pdf_text
from stratos.infrastructure.knowledge.scoring import rank
from stratos.infrastructure.mcp.client import McpStdioClient, build_server_env
from stratos.utils.html_text import html_to_text
from stratos.utils.redaction import SecretRedactor

runner = CliRunner()


# ================================ async API client (M07) ===============================
def aclient(routes: dict[Any, Any], **kw: Any) -> tuple[AsyncPlatformApiClient, list[float]]:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    client = AsyncPlatformApiClient(
        BASE, kw.pop("token", lambda: "tok"), transport=mock_transport(routes),
        sleep=fake_sleep, jitter=lambda: 0.0, **kw,
    )  # fmt: skip
    return client, sleeps


def test_async_client_headers_auth_and_json() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"ok": True})

    async def main() -> Any:
        client, _ = aclient({("GET", "/v1/x"): handler})
        async with client:
            return await client.get("/v1/x")

    assert run(main()) == {"ok": True}
    h = seen[0].headers
    assert h["Authorization"] == "Bearer tok" and h["X-Request-ID"] and h["X-Correlation-ID"]


def test_async_client_basic_scheme_and_default_headers() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={})

    async def main() -> None:
        client, _ = aclient(
            {("GET", "/a"): handler}, token=lambda: "dTpw", auth_scheme="Basic",
            default_headers={"X-Custom": "1"}, send_correlation=False,
        )  # fmt: skip
        async with client:
            await client.get("/a")

    run(main())
    assert seen[0].headers["Authorization"] == "Basic dTpw" and seen[0].headers["X-Custom"] == "1"
    assert "X-Correlation-ID" not in seen[0].headers


def test_async_client_retries_backoff_retry_after_and_limits() -> None:
    calls = iter([503, 503, 200])

    async def main() -> tuple[Any, list[float]]:
        client, sleeps = aclient(
            {("GET", "/a"): lambda r: httpx.Response(next(calls), json={"v": 1})}
        )
        async with client:
            return await client.get("/a"), sleeps

    result, sleeps = run(main())
    assert result == {"v": 1} and sleeps == [0.5, 1.0]

    async def rate_limited() -> list[float]:
        replies = iter(
            [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(201, json={})]
        )
        client, sleeps = aclient({("POST", "/a"): lambda r: next(replies)})
        async with client:
            await client.post("/a", json={})
        return sleeps

    assert run(rate_limited()) == [7.0]

    async def no_blind_retry() -> int:
        n = {"c": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            n["c"] += 1
            return httpx.Response(503)

        client, _ = aclient({("DELETE", "/a"): handler})
        async with client:
            with pytest.raises(APIError):
                await client.delete("/a")
        return n["c"]

    assert run(no_blind_retry()) == 1


def test_async_client_errors_network_pagination_and_tls() -> None:
    async def errors() -> None:
        client, _ = aclient({("GET", "/n"): httpx.Response(404, json={"message": "nope"})})
        async with client:
            with pytest.raises(ResourceNotFoundError):
                await client.get("/n")

    run(errors())

    async def network() -> int:
        def boom(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("slow")

        client, sleeps = aclient({("GET", "/a"): boom}, max_retries=2)
        async with client:
            with pytest.raises(NetworkError):
                await client.get("/a")
        return len(sleeps)

    assert run(network()) == 2

    async def pages() -> list[Any]:
        def p1(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=[1, 2], headers={"Link": f'<{BASE}/i?page=2>; rel="next"'}
            )

        def route(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[3]) if "page" in req.url.params else p1(req)

        client, _ = aclient({("GET", "/i"): route})
        async with client:
            return [x async for x in client.paginate("/i")]

    assert run(pages()) == [1, 2, 3]

    async def foreign() -> None:
        evil = httpx.Response(
            200, json=[1], headers={"Link": '<https://evil.example/x>; rel="next"'}
        )
        client, _ = aclient({("GET", "/i"): evil})
        async with client:
            with pytest.raises(APIError, match="different host"):
                _ = [x async for x in client.paginate("/i")]

    run(foreign())
    with pytest.raises(ConfigurationError):
        AsyncPlatformApiClient("http://api.example.com")


# ================================ GitHub extras (M11) ===================================
def test_github_branches_prs_issues() -> None:
    pr = {"number": 7, "title": "Add x", "state": "open", "user": {"login": "dev"},
          "head": {"ref": "feat"}, "base": {"ref": "main"}, "draft": True, "html_url": "u"}  # fmt: skip
    issues = [
        {
            "number": 1,
            "title": "Bug",
            "state": "open",
            "user": {"login": "a"},
            "labels": [{"name": "bug"}],
        },
        {"number": 7, "title": "Add x", "state": "open", "pull_request": {}, "labels": []},
    ]
    seen: dict[str, Any] = {}

    def pulls(req: httpx.Request) -> httpx.Response:
        seen["state"] = req.url.params["state"]
        return httpx.Response(200, json=[pr])

    svc = gh(
        {
            ("GET", "/repos/acme/api/branches"): httpx.Response(
                200, json=[{"name": "main", "protected": True}, {"name": "dev"}]
            ),
            ("GET", "/repos/acme/api/pulls"): pulls,
            ("GET", "/repos/acme/api/issues"): httpx.Response(200, json=issues),
        }
    )
    assert [(b.name, b.protected) for b in svc.list_branches("acme", "api")] == [
        ("main", True),
        ("dev", False),
    ]
    prs = svc.list_pull_requests("acme", "api", state="all")
    assert prs[0].number == 7 and prs[0].draft and prs[0].head == "feat" and seen["state"] == "all"
    found = svc.list_issues("acme", "api")
    assert [i.number for i in found] == [1] and found[0].labels == ("bug",)  # PRs excluded
    with pytest.raises(ValidationError, match="Invalid state"):
        svc.list_pull_requests("acme", "api", state="bogus")


def test_github_label_sync_creates_updates_and_is_idempotent() -> None:
    existing = [
        {"name": "bug", "color": "ff0000", "description": "Broken"},
        {"name": "good first issue", "color": "000000", "description": ""},
    ]
    posts: list[dict[str, Any]] = []
    patches: list[tuple[str, dict[str, Any]]] = []

    def create(req: httpx.Request) -> httpx.Response:
        posts.append(json.loads(req.content))
        return httpx.Response(201, json={})

    def labels(req: httpx.Request) -> httpx.Response:
        return create(req) if req.method == "POST" else httpx.Response(200, json=existing)

    def patch(req: httpx.Request) -> httpx.Response:
        patches.append((req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={})

    svc = gh(
        {
            ("GET", "/repos/acme/api/labels"): labels,
            ("POST", "/repos/acme/api/labels"): labels,
            ("PATCH", "/repos/acme/api/labels/good first issue"): patch,
        }
    )
    desired = [
        Label(name="bug", color="ff0000", description="Broken"),  # unchanged
        Label(name="feature", color="00ff00", description="New"),  # created
        Label(name="good first issue", color="7057ff", description="Easy"),  # updated
    ]
    assert svc.upsert_labels("acme", "api", desired) == (1, 1)
    assert posts == [{"name": "feature", "color": "00ff00", "description": "New"}]
    assert patches == [
        ("/repos/acme/api/labels/good first issue", {"color": "7057ff", "description": "Easy"})
    ]


def test_github_actions_policy_and_security() -> None:
    bodies: dict[str, Any] = {}

    def cap(key: str, status: int = 200) -> Callable[[httpx.Request], httpx.Response]:
        def handler(req: httpx.Request) -> httpx.Response:
            bodies[key] = json.loads(req.content) if req.content else None
            return httpx.Response(
                status,
                json=REPO if req.method == "PATCH" else {"message": "no"} if status >= 400 else {},
            )

        return handler

    svc = gh(
        {
            ("GET", "/repos/acme/api/actions/workflows"): httpx.Response(
                200,
                json={
                    "workflows": [
                        {
                            "id": 1,
                            "name": "CI",
                            "state": "active",
                            "path": ".github/workflows/ci.yml",
                        }
                    ]
                },
            ),
            ("GET", "/repos/acme/api/actions/runs"): httpx.Response(
                200,
                json={
                    "workflow_runs": [
                        {
                            "id": 9,
                            "name": "CI",
                            "status": "completed",
                            "conclusion": "success",
                            "head_branch": "main",
                            "html_url": "u",
                        }
                    ]
                },
            ),
            ("PUT", "/repos/acme/api/actions/permissions"): cap("actions"),
            ("PATCH", "/repos/acme/api"): cap("patch"),
            ("PUT", "/repos/acme/api/vulnerability-alerts"): cap("alerts", 204),
            ("PUT", "/repos/acme/api/automated-security-fixes"): cap("fixes", 422),
        }
    )
    assert svc.list_workflows("acme", "api")[0].name == "CI"
    assert svc.list_workflow_runs("acme", "api")[0].conclusion == "success"
    svc.configure_actions("acme", "api")
    assert bodies["actions"] == {"enabled": True, "allowed_actions": "selected"}
    with pytest.raises(ValidationError):
        svc.configure_actions("acme", "api", allowed_actions="everything")
    svc.apply_repo_policy("acme", "api")
    assert (
        bodies["patch"]["allow_merge_commit"] is False
        and bodies["patch"]["delete_branch_on_merge"] is True
    )
    results = svc.apply_security("acme", "api")
    assert results[0].endswith("enabled") and results[1].endswith("enabled")
    assert "unavailable" in results[2]  # one feature missing must not fail the rest


class RichFake(FakeGitHub):
    """FakeGitHub plus the new port methods."""

    def __init__(self) -> None:
        super().__init__()
        self.labels_synced: list[Label] = []
        self.policy_applied = self.actions_set = 0

    def list_branches(self, org: str, repo: str, *, limit: int = 100) -> list[Any]:
        from stratos.domain.models.github import Branch

        return [Branch(name="main", protected=True)]

    def list_pull_requests(
        self, org: str, repo: str, *, state: str = "open", limit: int = 50
    ) -> list[Any]:
        from stratos.domain.models.github import PullRequest

        return [PullRequest(number=1, title="T", state=state)]

    def list_issues(
        self, org: str, repo: str, *, state: str = "open", limit: int = 50
    ) -> list[Any]:
        from stratos.domain.models.github import Issue

        return [Issue(number=2, title="I", state=state, labels=("bug", "ui"))]

    def list_labels(self, org: str, repo: str) -> list[Label]:
        return [Label(name="bug")]

    def upsert_labels(self, org: str, repo: str, desired: list[Label]) -> tuple[int, int]:
        self.labels_synced = desired
        return len(desired), 0

    def list_workflows(self, org: str, repo: str) -> list[Any]:
        from stratos.domain.models.github import Workflow

        return [Workflow(id=1, name="CI", state="active")]

    def list_workflow_runs(self, org: str, repo: str, *, limit: int = 20) -> list[Any]:
        from stratos.domain.models.github import WorkflowRun

        return [WorkflowRun(id=1, name="CI", status="completed", conclusion="success")]

    def configure_actions(self, org: str, repo: str, *, allowed_actions: str = "selected") -> None:
        self.actions_set += 1

    def apply_repo_policy(self, org: str, repo: str) -> None:
        self.policy_applied += 1

    def apply_security(self, org: str, repo: str) -> list[str]:
        return ["secret scanning: enabled"]


def test_repository_service_full_configure_reads_and_dry_run(tmp_path: Path) -> None:
    svc, fake, *_ = make_repo_service(tmp_path, "maintainer")
    rich = RichFake()
    rich.repos["acme/api"] = fake.repos.get("acme/api") or __import__(
        "stratos.domain.models.github", fromlist=["Repository"]
    ).Repository(name="api", full_name="acme/api")
    svc._gh = rich  # type: ignore[assignment]
    done = svc.configure(
        "api",
        branch_protection=False,
        labels=[Label(name="bug")],
        policy=True,
        security=True,
        actions=True,
    )
    assert "labels: 1 created, 0 updated" in done and "merge policy applied" in done
    assert "secret scanning: enabled" in done and rich.policy_applied == 1 and rich.actions_set == 1
    assert (
        svc.branches("api")[0].protected and svc.pull_requests("api", state="all")[0].state == "all"
    )
    assert svc.issues("api")[0].labels == ("bug", "ui") and svc.labels("api")[0].name == "bug"
    assert (
        svc.workflows("api")[0].name == "CI" and svc.workflow_runs("api")[0].status == "completed"
    )
    assert audit_results(tmp_path) == [("repo.configure", "success")]
    dry, _, out, err, _ = make_repo_service(tmp_path / "d", "maintainer", dry_run=True)
    dry._gh = rich  # type: ignore[assignment]
    assert dry.configure("api", branch_protection=False, policy=True) == []
    assert rich.policy_applied == 1 and "DRY RUN" in out.export_text() + err.export_text()


def test_cli_repo_configure_labels_and_read_commands(
    cli_sandbox: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = cli_sandbox("maintainer")
    from stratos.cli import context

    fake = RichFake()
    from stratos.domain.models.github import Repository

    fake.repos["Stratos-Technologies-fzco/api"] = Repository(
        name="api", full_name="Stratos-Technologies-fzco/api"
    )
    monkeypatch.setattr(context.CliContext, "github", property(lambda self: fake), raising=False)
    (project / "labels.yaml").write_text(
        "- name: bug\n  color: '#FF0000'\n  description: Broken\n- name: ui\n"
    )
    r = runner.invoke(
        app,
        [
            "repo",
            "configure",
            "api",
            "--no-protect",
            "--labels",
            "labels.yaml",
            "--policy",
            "--security",
            "--actions",
        ],
    )
    assert r.exit_code == 0, r.output
    assert [(lb.name, lb.color) for lb in fake.labels_synced] == [
        ("bug", "ff0000"),
        ("ui", "ededed"),
    ]
    (project / "bad.yaml").write_text("just: a mapping\n")
    assert runner.invoke(app, ["repo", "configure", "api", "--labels", "bad.yaml"]).exit_code == 6
    for cmd, key in (("branches", "name"), ("prs", "number"), ("issues", "number"), ("labels", "name"),
                     ("workflows", "name"), ("runs", "id")):  # fmt: skip
        out = runner.invoke(app, ["-o", "json", "repo", cmd, "api"])
        assert out.exit_code == 0, (cmd, out.output)
        assert key in json.loads(out.output)[0]


# ================================ OpenAI / Azure / tool chat (M16) ============================
class FakeOpenAI:
    def __init__(self, error: Exception | None = None, tool_call: bool = False) -> None:
        self.error, self.tool_call = error, tool_call
        self.calls: list[dict[str, Any]] = []
        outer = self

        class Completions:
            async def create(self, **kw: Any) -> Any:
                outer.calls.append(kw)
                if outer.error:
                    raise outer.error
                if kw.get("stream"):

                    async def gen() -> Any:
                        for t in ("a", "b"):
                            yield SimpleNamespace(
                                choices=[SimpleNamespace(delta=SimpleNamespace(content=t))]
                            )

                    return gen()
                calls = (
                    [
                        SimpleNamespace(
                            id="c1",
                            function=SimpleNamespace(
                                name="files_read", arguments='{"path": "a.txt"}'
                            ),
                        )
                    ]
                    if outer.tool_call
                    else None
                )
                message = SimpleNamespace(content="hi", tool_calls=calls)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=message, finish_reason="stop")],
                    usage=SimpleNamespace(prompt_tokens=5, completion_tokens=7),
                )

        class Models:
            async def list(self) -> Any:
                return SimpleNamespace(data=[SimpleNamespace(id="gpt-x")])

        self.chat = SimpleNamespace(completions=Completions())
        self.models = Models()


def test_openai_provider_ask_stream_models_and_tool_calls() -> None:
    fake = FakeOpenAI()
    p = OpenAIProvider("k", default_model="gpt-x", client=fake)
    res = run(p.ask("hello", system="be brief", max_tokens=33))
    assert res.text == "hi" and res.input_tokens == 5 and res.output_tokens == 7
    sent = fake.calls[0]
    assert (
        sent["messages"][0] == {"role": "system", "content": "be brief"}
        and sent["max_completion_tokens"] == 33
    )

    async def chunks() -> list[str]:
        return [c async for c in p.stream("hello")]

    assert run(chunks()) == ["a", "b"] and run(p.list_models())[0].id == "gpt-x"
    tool_fake = FakeOpenAI(tool_call=True)
    tp = OpenAIProvider("k", default_model="gpt-x", client=tool_fake)
    from stratos.domain.models.extensions import ToolSpec

    turn = run(
        tp.chat([ChatMessage(role="user", content="go")], tools=[ToolSpec(name="files_read")])
    )
    assert turn.tool_calls == (ToolCall(id="c1", name="files_read", arguments={"path": "a.txt"}),)
    assert tool_fake.calls[0]["tools"][0]["function"]["name"] == "files_read"


def test_openai_message_conversion_round_trip() -> None:
    msgs = [
        ChatMessage(role="user", content="q"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=(ToolCall(id="c1", name="t", arguments={"a": 1}),),
        ),
        ChatMessage(role="tool", content="result", tool_call_id="c1"),
    ]
    out = to_openai_messages(msgs, "sys")
    assert (
        out[0]["role"] == "system"
        and out[2]["tool_calls"][0]["function"]["arguments"] == '{"a": 1}'
    )
    assert out[2]["content"] is None and out[3] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "result",
    }


def test_anthropic_chat_tool_use_and_message_conversion() -> None:
    msgs = [
        ChatMessage(role="user", content="q"),
        ChatMessage(role="assistant", content="thinking", tool_calls=(
            ToolCall(id="t1", name="a", arguments={}), ToolCall(id="t2", name="b", arguments={"x": 1}))),
        ChatMessage(role="tool", content="r1", tool_call_id="t1"),
        ChatMessage(role="tool", content="r2", tool_call_id="t2"),
    ]  # fmt: skip
    out = to_anthropic_messages(msgs)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert [b["type"] for b in out[1]["content"]] == ["text", "tool_use", "tool_use"]
    assert [b["tool_use_id"] for b in out[2]["content"]] == [
        "t1",
        "t2",
    ]  # results merged in one message

    captured: dict[str, Any] = {}

    class Messages:
        async def create(self, **kw: Any) -> Any:
            captured.update(kw)
            return SimpleNamespace(
                content=[
                    SimpleNamespace(type="text", text="plan"),
                    SimpleNamespace(
                        type="tool_use", id="tu1", name="files_read", input={"path": "x"}
                    ),
                ],
                stop_reason="tool_use",
                usage=SimpleNamespace(input_tokens=4, output_tokens=6),
            )

    p = AnthropicProvider("k", default_model="m", client=SimpleNamespace(messages=Messages()))
    from stratos.domain.models.extensions import ToolSpec

    turn = run(
        p.chat(
            [ChatMessage(role="user", content="q")],
            tools=[ToolSpec(name="files_read", description="d")],
        )
    )
    assert turn.text == "plan" and turn.stop_reason == "tool_use"
    assert (
        turn.tool_calls[0].arguments == {"path": "x"}
        and captured["tools"][0]["name"] == "files_read"
    )


def test_openai_errors_azure_and_factory() -> None:
    import openai

    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")

    def status_err(cls: type[Exception], code: int) -> Exception:
        return cls("boom", response=httpx.Response(code, request=req), body=None)  # type: ignore[call-arg]

    cases: list[tuple[Exception, type[Exception]]] = [
        (status_err(openai.AuthenticationError, 401), AuthenticationError),
        (status_err(openai.PermissionDeniedError, 403), AuthorizationError),
        (status_err(openai.RateLimitError, 429), APIError),
        (openai.APIConnectionError(request=req), NetworkError),
        (status_err(openai.InternalServerError, 500), APIError),
    ]
    for source, expected in cases:
        p = OpenAIProvider("k", default_model="m", client=FakeOpenAI(error=source))
        with pytest.raises(expected):
            run(p.ask("hi"))
    with pytest.raises(AuthenticationError, match="No API key"):
        run(OpenAIProvider(None, default_model="m").ask("hi"))
    with pytest.raises(APIError, match="Azure OpenAI endpoint"):
        run(AzureOpenAIProvider("k", endpoint=None, api_version="v", default_model="d").ask("hi"))
    azure = create_provider(
        "azure-openai",
        api_key="k",
        default_model="dep",
        azure_endpoint="https://x.openai.azure.com",
    )
    assert (
        azure.name == "azure-openai"
        and create_provider("openai", api_key="k", default_model="m").name == "openai"
    )
    assert [api_key_env_for(n) for n in ("claude", "openai", "azure-openai")] == [
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY",
    ]  # fmt: skip


# ================================ MCP stdio client + agent runner (M15, M17) ===================
SERVER_SCRIPT = r"""
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    print("stray non-json output", flush=True)
    if "id" not in msg:
        continue
    method = msg["method"]
    if method == "initialize":
        res = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}
    elif method == "tools/list":
        if "hang" in sys.argv:
            continue
        res = {"tools": [
            {"name": "echo", "description": "Echo", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
            {"name": "boom", "description": "Fails"},
            {"name": "leak", "description": "Leaks"}]}
    elif method == "tools/call":
        name, args = msg["params"]["name"], msg["params"]["arguments"]
        if name == "echo":
            res = {"content": [{"type": "text", "text": "echo: " + args.get("text", "")}]}
        elif name == "boom":
            res = {"content": [{"type": "text", "text": "it broke"}], "isError": True}
        else:
            res = {"content": [{"type": "text", "text": "key token=abc123secretxyz"}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "nope"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
"""


@pytest.fixture
def mcp_server(tmp_path: Path) -> Path:
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(SERVER_SCRIPT)
    return script


def test_build_server_env_is_minimal_and_resolves_references() -> None:
    environ = {"PATH": "/bin", "HOME": "/h", "SECRET_ELSEWHERE": "no", "GITHUB_TOKEN": "tok"}
    env = build_server_env(
        {"GITHUB_TOKEN": "${GITHUB_TOKEN}", "MISSING": "${NOPE}", "LOG": "debug"}, environ
    )
    assert env == {"PATH": "/bin", "HOME": "/h", "GITHUB_TOKEN": "tok", "LOG": "debug"}
    assert "SECRET_ELSEWHERE" not in env  # the caller's full environment is never passed on


def test_mcp_client_lists_and_calls_tools(mcp_server: Path) -> None:
    async def main() -> Any:
        env = build_server_env({}, __import__("os").environ)
        async with McpStdioClient(sys.executable, [str(mcp_server)], env, timeout=10) as client:
            tools = await client.list_tools()
            ok = await client.call_tool("echo", {"text": "hi"})
            bad = await client.call_tool("boom", {})
        return tools, ok, bad, client._proc

    tools, ok, bad, proc = run(main())
    assert [t.name for t in tools] == ["echo", "boom", "leak"] and tools[0].input_schema[
        "properties"
    ]
    assert ok == ("echo: hi", False) and bad == ("it broke", True)
    assert proc is None  # the server process was shut down


def test_mcp_client_missing_program_and_timeout(mcp_server: Path) -> None:
    async def missing() -> None:
        await McpStdioClient("definitely-not-a-real-program-xyz", [], {}).start()

    with pytest.raises(DependencyError):
        run(missing())

    async def hang() -> None:
        env = build_server_env({}, __import__("os").environ)
        client = McpStdioClient(sys.executable, [str(mcp_server), "hang"], env, timeout=1.0)
        await client.start()
        try:
            await client.list_tools()
        finally:
            await client.close()

    with pytest.raises(APIError, match="did not answer"):
        run(hang())


class Scripted:
    """Provider that returns scripted chat turns."""

    name = "scripted"

    def __init__(self, turns: list[ChatTurn | Exception]) -> None:
        self.turns = list(turns)
        self.seen: list[dict[str, Any]] = []

    async def chat(
        self,
        messages: Any,
        *,
        tools: Any = (),
        model: Any = None,
        system: Any = None,
        max_tokens: int = 0,
    ) -> ChatTurn:
        self.seen.append(
            {
                "messages": list(messages),
                "tools": [t.name for t in tools],
                "system": system,
                "model": model,
            }
        )
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn

    async def ask(self, *a: Any, **k: Any) -> Any: ...
    async def stream(self, *a: Any, **k: Any) -> Any: ...
    async def list_models(self) -> Any: ...


def call(name: str, **args: Any) -> ChatTurn:
    return ChatTurn(
        model="m",
        tool_calls=(ToolCall(id="c1", name=name, arguments=args),),
        input_tokens=2,
        output_tokens=3,
    )


def done(text: str = "final") -> ChatTurn:
    return ChatTurn(model="m", text=text, input_tokens=1, output_tokens=1)


def make_runner(tmp_path: Path, provider: Scripted, **kw: Any) -> tuple[AgentRunner, Path]:
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    require, _ = make_require("viewer")
    knowledge = KnowledgeService(lambda: [make_provider(tmp_path)], require)
    return AgentRunner(
        lambda: provider, knowledge, project, SecretRedactor(),
        default_model="m", max_tokens=256, **kw,
    ), project  # fmt: skip


READER = AgentDefinition(
    name="reader", instructions="Read things", tools=("files.read", "files.list"),
    permissions=(AgentPermission.REPO_READ,),
)  # fmt: skip


def test_agent_tool_loop_reads_files_with_redaction_and_totals(tmp_path: Path) -> None:
    prov = Scripted([call("files_read", path="src/app.py"), done("looks fine")])
    r, project = make_runner(tmp_path, prov)
    (project / "src").mkdir()
    (project / "src" / "app.py").write_text("x = 1\npassword=hunter2hunter2\n")
    result = run(r.run(READER, "review src/app.py"))
    assert result.text == "looks fine" and result.steps == 2 and result.tools_used == ["files_read"]
    assert result.input_tokens == 3 and result.output_tokens == 4  # summed over both turns
    tool_msg = prov.seen[1]["messages"][-1]
    assert tool_msg.role == "tool" and "x = 1" in tool_msg.content
    assert "hunter2hunter2" not in tool_msg.content and "[REDACTED]" in tool_msg.content
    assert prov.seen[0]["tools"] == ["files_read", "files_list"]


@pytest.mark.parametrize(
    "target", [".env", "../outside.txt", ".git/config", "keys/server.pem", ".mcp.json"]
)
def test_agent_file_tools_refuse_secrets_and_escapes(tmp_path: Path, target: str) -> None:
    prov = Scripted([call("files_read", path=target), done()])
    r, project = make_runner(tmp_path, prov)
    (project / ".env").write_text("API=1")
    (tmp_path / "outside.txt").write_text("OUTSIDE-FILE-CONTENT")
    (project / ".git").mkdir()
    (project / ".git" / "config").write_text("[core]")
    (project / "keys").mkdir()
    (project / "keys" / "server.pem").write_text("-----BEGIN")
    (project / ".mcp.json").write_text("{}")
    run(r.run(READER, "go"))
    result_text = prov.seen[1]["messages"][-1].content
    assert (
        result_text.startswith("Error:")
        and "API=1" not in result_text
        and "OUTSIDE-FILE-CONTENT" not in result_text
    )


def test_agent_file_list_skips_denied_and_resolve_inside(tmp_path: Path) -> None:
    prov = Scripted([call("files_list"), done()])
    r, project = make_runner(tmp_path, prov)
    (project / "a.py").write_text("1")
    (project / ".env").write_text("2")
    (project / "node_modules").mkdir()
    (project / "node_modules" / "x.js").write_text("3")
    run(r.run(READER, "list"))
    listing = prov.seen[1]["messages"][-1].content
    assert "a.py" in listing and ".env" not in listing and "node_modules" not in listing
    with pytest.raises(ValidationError):
        resolve_inside(project, "..")


def test_agent_tool_permission_checked_on_every_call_and_unknown_tools(tmp_path: Path) -> None:
    prov = Scripted([call("files_read", path="a.txt"), call("delete_everything"), done()])
    r, project = make_runner(tmp_path, prov)
    (project / "a.txt").write_text("SECRET-CONTENT")
    no_permission = AgentDefinition(
        name="x", instructions="i", tools=("files.read",), permissions=()
    )
    run(r.run(no_permission, "go"))
    first, second = prov.seen[1]["messages"][-1].content, prov.seen[2]["messages"][-1].content
    assert "not permitted" in first and "SECRET-CONTENT" not in first
    assert "unknown tool" in second


def test_agent_stops_after_max_steps(tmp_path: Path) -> None:
    prov = Scripted([call("files_list")] * 5)
    r, project = make_runner(tmp_path, prov, max_steps=3)
    with pytest.raises(APIError, match="did not finish within 3 steps"):
        run(r.run(READER, "loop forever"))
    assert len(prov.seen) == 3


def test_agent_skills_become_instructions_and_missing_skill_fails(tmp_path: Path) -> None:
    prov = Scripted([done()])
    r, _ = make_runner(
        tmp_path, prov, skill_loader=lambda n: "Always be kind." if n == "kindness" else None
    )
    agent = AgentDefinition(name="s", instructions="Base rules", skills=("kindness",))
    run(r.run(agent, "hi"))
    system = prov.seen[0]["system"]
    assert (
        "Base rules" in system
        and '<skill name="kindness">' in system
        and "Always be kind." in system
    )
    with pytest.raises(ValidationError, match="not installed"):
        run(r.run(AgentDefinition(name="s", instructions="i", skills=("nope",)), "hi"))


def test_agent_knowledge_get_tool(tmp_path: Path) -> None:
    prov = Scripted([call("knowledge_get", id="docs:runbook.md"), done()])
    r, _ = make_runner(tmp_path, prov)
    agent = AgentDefinition(
        name="k",
        instructions="i",
        tools=("knowledge.get",),
        permissions=(AgentPermission.KNOWLEDGE_READ,),
    )
    run(r.run(agent, "how do we roll back"))
    text = prov.seen[1]["messages"][-1].content
    assert "Deployment Runbook" in text and "supersecretvalue1" not in text


def mcp_agent(**kw: Any) -> AgentDefinition:
    return AgentDefinition(
        name="m", instructions="use tools", mcp_servers=("fake",),
        permissions=(AgentPermission.RUN_COMMANDS,), **kw,
    )  # fmt: skip


def server_config(script: Path) -> dict[str, dict[str, Any]]:
    return {"fake": {"command": sys.executable, "args": [str(script)]}}


def test_agent_runs_mcp_tools_and_always_shuts_servers_down(
    tmp_path: Path, mcp_server: Path
) -> None:
    started: list[McpStdioClient] = []

    def factory(name: str, config: dict[str, Any]) -> McpStdioClient:
        client = McpStdioClient(
            config["command"],
            config["args"],
            build_server_env({}, __import__("os").environ),
            timeout=10,
        )
        started.append(client)
        return client

    calls = [
        ChatTurn(model="m", tool_calls=(
            ToolCall(id="1", name="mcp__fake__echo", arguments={"text": "hi"}),
            ToolCall(id="2", name="mcp__fake__boom", arguments={}),
            ToolCall(id="3", name="mcp__fake__leak", arguments={}))),
        done("ok"),
    ]  # fmt: skip
    prov = Scripted(calls)
    r, _ = make_runner(
        tmp_path, prov, mcp_configs=lambda: server_config(mcp_server), mcp_factory=factory
    )
    result = run(r.run(mcp_agent(), "use the server"))
    assert result.text == "ok" and prov.seen[0]["tools"] == [
        "mcp__fake__echo",
        "mcp__fake__boom",
        "mcp__fake__leak",
    ]
    results = [m.content for m in prov.seen[1]["messages"] if m.role == "tool"]
    assert results[0] == "echo: hi" and results[1] == "Error: it broke"
    assert "abc123secretxyz" not in results[2] and "[REDACTED]" in results[2]
    assert started and all(c._proc is None for c in started)  # closed after the run


def test_agent_mcp_guards_permission_policy_and_installation(
    tmp_path: Path, mcp_server: Path
) -> None:
    prov = Scripted([done(), done(), done()])
    cfg = lambda: server_config(mcp_server)  # noqa: E731
    r, _ = make_runner(tmp_path, prov, mcp_configs=cfg, mcp_allowed=lambda n: False)
    with pytest.raises(AuthorizationError, match="organisation policy"):
        run(r.run(mcp_agent(), "x"))
    r2, _ = make_runner(tmp_path / "b", prov, mcp_configs=cfg)
    with pytest.raises(AuthorizationError, match="run_commands"):
        run(r2.run(AgentDefinition(name="m", instructions="i", mcp_servers=("fake",)), "x"))
    r3, _ = make_runner(tmp_path / "c", prov, mcp_configs=dict)
    with pytest.raises(ValidationError, match="not installed"):
        run(r3.run(mcp_agent(), "x"))
    assert prov.seen == []  # the model was never called


def test_agent_mcp_server_failure_is_cleaned_up(tmp_path: Path, mcp_server: Path) -> None:
    clients: list[McpStdioClient] = []

    def factory(name: str, config: dict[str, Any]) -> McpStdioClient:
        c = McpStdioClient(
            sys.executable,
            [str(mcp_server), "hang"],
            build_server_env({}, __import__("os").environ),
            timeout=1.0,
        )
        clients.append(c)
        return c

    r, _ = make_runner(
        tmp_path,
        Scripted([done()]),
        mcp_configs=lambda: server_config(mcp_server),
        mcp_factory=factory,
    )
    with pytest.raises(APIError):
        run(r.run(mcp_agent(), "x"))
    assert clients and all(c._proc is None for c in clients)


def test_agent_service_validates_new_definitions_and_rejects_widened_permissions(
    tmp_path: Path,
) -> None:
    prov = Scripted([done()])
    runner_, project = make_runner(tmp_path, prov)
    require, ident = make_require("maintainer")
    guard, *_ = make_guard(yes=True)
    svc = AgentService(
        FilesystemAgentRegistry(project), FilesystemAgentRunLog(project), runner_, require,
        make_audit(tmp_path, ident), guard,
        allowed_permissions={AgentPermission.KNOWLEDGE_READ, AgentPermission.REPO_READ},
        default_model="m", clock=lambda: NOW,
    )  # fmt: skip
    with pytest.raises(ValidationError, match="run_commands"):
        svc.create(AgentDefinition(name="a", instructions="i", mcp_servers=("fake",)))
    with pytest.raises(ValidationError):
        svc.create(AgentDefinition(name="b", instructions="i", skills=("Bad Skill!",)))
    with pytest.raises(ValidationError, match="requires the 'repo_read'"):
        svc.create(AgentDefinition(name="c", instructions="i", tools=("files.list",)))
    svc.create(mcp_agent().model_copy(update={"name": "mcp-agent"}))
    with pytest.raises(AuthorizationError, match="run_commands"):
        run(svc.run("mcp-agent", "x"))  # not allowed by default: admin must opt in
    assert prov.seen == [] and ("agent.run", "denied") in audit_results(tmp_path)
    saved = svc.get("code-review")
    assert saved.name in BUILTIN_AGENTS and run(svc.run("code-review", "review this")).steps == 1


# ================================ remote knowledge sources (M18) ================================
def async_transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def make_gh_knowledge(
    tmp_path: Path, files: dict[str, str], shas: dict[str, str] | None = None
) -> Any:
    shas = shas or {}

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/repos/acme/docs":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/repos/acme/docs/git/trees/main":
            tree = [
                {"type": "blob", "path": p, "sha": shas.get(p, "s-" + p), "size": len(t)}
                for p, t in files.items()
            ]
            tree += [{"type": "tree", "path": "guide"}, {"type": "blob", "path": "img.png", "sha": "x", "size": 3},
                     {"type": "blob", "path": "guide/huge.md", "sha": "h", "size": 9_999_999}]  # fmt: skip
            return httpx.Response(200, json={"tree": tree})
        prefix = "/repos/acme/docs/contents/"
        if path.startswith(prefix) and path[len(prefix) :] in files:
            assert req.headers["Accept"] == "application/vnd.github.raw+json"
            return httpx.Response(200, text=files[path[len(prefix) :]])
        return httpx.Response(404, json={"message": "Not Found"})

    client = AsyncPlatformApiClient(
        BASE, lambda: "t", transport=async_transport(handler), send_correlation=False
    )
    return remote.GitHubKnowledgeProvider(
        client, ["acme/docs:guide"], tmp_path / "gh-manifest.json", clock=lambda: NOW
    )


def test_github_knowledge_search_get_sync_status(tmp_path: Path) -> None:
    files = {
        "guide/deploy.md": "# Deployment Runbook\n\nRoll back by redeploying. token=supersecretvalue1\n",
        "guide/style.md": "# Style\n\nMentions deployment once.\n",
        "other/notes.md": "# Notes\n\nNot under the configured path.\n",
    }
    p = make_gh_knowledge(tmp_path, files)

    async def main() -> Any:
        try:
            hits = await p.search("deployment runbook")
            doc = await p.get("github:acme/docs:guide/deploy.md")
            outside = await p.get("github:acme/docs:other/notes.md")
            missing = await p.get("github:acme/docs:nope.md")
            first = await p.status()
            synced = await p.sync()
            after = await p.status()
            return hits, doc, outside, missing, first, synced, after
        finally:
            await p.aclose()

    hits, doc, outside, missing, first, synced, after = run(main())
    assert [h.id for h in hits][0] == "github:acme/docs:guide/deploy.md" and len(hits) == 2
    assert (
        "supersecretvalue1" not in hits[0].snippet
        and doc
        and "supersecretvalue1" not in doc.content
    )
    assert outside is None and missing is None  # only documents under the configured path
    assert first.stale == 2 and synced.documents == 2 and after.stale == 0
    assert "guide/huge.md" not in (tmp_path / "gh-manifest.json").read_text()  # oversized skipped
    changed = make_gh_knowledge(tmp_path, files, shas={"guide/deploy.md": "new-sha"})
    assert run(changed.status()).stale == 1  # a changed blob is detected


def test_github_knowledge_rejects_bad_sources(tmp_path: Path) -> None:
    client = AsyncPlatformApiClient(BASE, lambda: "t")
    for bad in ("justrepo", "acme/../x", "a b/c"):
        with pytest.raises((ConfigurationError, ValidationError)):
            remote.GitHubKnowledgeProvider(client, [bad], tmp_path / "m.json")


def test_confluence_search_get_status_and_credentials(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/wiki/rest/api/search":
            return httpx.Response(200, json={"results": [
                {"title": "Runbook", "excerpt": "roll <b>back</b> token=abc123secretxyz", "content": {"id": "111"}},
                {"title": "Guide", "excerpt": "", "content": {"id": "222"}},
                {"title": "Space home"},  # no content id: skipped
            ]})  # fmt: skip
        if req.url.path == "/wiki/rest/api/content/111":
            html = "<h1>Runbook</h1><p>Roll back <b>fast</b>.</p><script>x()</script><p>key token=abc123secretxyz</p>"
            return httpx.Response(
                200, json={"title": "Runbook", "body": {"storage": {"value": html}}}
            )
        if req.url.path == "/wiki/rest/api/space":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404, json={"message": "no"})

    auth = remote.basic_auth_from_env(
        {"CONFLUENCE_EMAIL": "me@x.io", "CONFLUENCE_API_TOKEN": "tok"}
    )
    client = AsyncPlatformApiClient(
        "https://acme.atlassian.net",
        auth,
        auth_scheme="Basic",
        transport=async_transport(handler),
        send_correlation=False,
    )
    p = remote.ConfluenceKnowledgeProvider(client, spaces=["ENG", "OPS"])

    async def main() -> Any:
        try:
            hits = await p.search('roll "back" \\ now')
            doc = await p.get("confluence:111")
            return (
                hits,
                doc,
                await p.get("confluence:abc"),
                await p.get("confluence:999"),
                await p.status(),
            )
        finally:
            await p.aclose()

    hits, doc, bad_id, missing, status = run(main())
    cql = seen[0].url.params["cql"]
    assert 'type=page AND text ~ "roll  back   now"' in cql or '"roll' in cql
    assert (
        '"' not in cql.split("text ~ ")[1].split(" AND space")[0].strip('"')
        and 'space in ("ENG","OPS")' in cql
    )
    assert [h.id for h in hits] == ["confluence:111", "confluence:222"] and hits[0].score > hits[
        1
    ].score
    assert "abc123secretxyz" not in hits[0].snippet and "<b>" not in hits[0].snippet
    assert (
        doc
        and "Roll back fast." in doc.content
        and "x()" not in doc.content
        and "abc123secretxyz" not in doc.content
    )
    assert bad_id is None and missing is None and status.detail and status.documents == 0
    expected = "Basic " + base64.b64encode(b"me@x.io:tok").decode()
    assert seen[0].headers["Authorization"] == expected
    with pytest.raises(AuthenticationError, match="credentials are missing") as missing_creds:
        remote.basic_auth_from_env({})()
    assert "CONFLUENCE_EMAIL" in (missing_creds.value.hint or "")
    with pytest.raises(ConfigurationError):
        remote.ConfluenceKnowledgeProvider(client, spaces=['E"NG'])


def test_sharepoint_search_get_and_safety(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/v1.0/sites/contoso.sharepoint.com:/sites/Eng":
            return httpx.Response(200, json={"id": "contoso.sharepoint.com,guid1,guid2"})
        if "/drive/root/search" in path:
            assert (
                "It''s" in path or "It%27%27s" in req.url.raw_path.decode()
            )  # OData quote escaping
            return httpx.Response(200, json={"value": [
                {"id": "ITEM1", "name": "Runbook.md", "file": {}, "description": "roll back token=abc123secretxyz"},
                {"id": "ITEM2", "name": "Budget.xlsx", "file": {}},
                {"id": "FOLDER", "name": "Docs", "folder": {}},
                {"id": "ITEM3", "name": "Notes.html", "file": {}}]})  # fmt: skip
        if path.endswith("/drive/items/ITEM1"):
            return httpx.Response(
                200,
                json={"name": "Runbook.md", "@microsoft.graph.downloadUrl": "https://dl.example/1"},
            )
        if path.endswith("/drive/items/ITEM2"):
            return httpx.Response(
                200,
                json={
                    "name": "Budget.xlsx",
                    "@microsoft.graph.downloadUrl": "https://dl.example/2",
                },
            )
        if path.endswith("/drive/items/ITEM3"):
            return httpx.Response(
                200,
                json={"name": "Notes.html", "@microsoft.graph.downloadUrl": "https://dl.example/3"},
            )
        return httpx.Response(404, json={"message": "no"})

    downloads: list[str] = []

    async def download(url: str) -> bytes:
        downloads.append(url)
        return (
            b"<p>Roll back</p> token=abc123secretxyz"
            if url.endswith("/3")
            else b"# Runbook\nRoll back. token=abc123secretxyz"
        )

    client = AsyncPlatformApiClient(
        "https://graph.microsoft.com",
        lambda: "tok",
        transport=async_transport(handler),
        send_correlation=False,
    )
    p = remote.SharePointKnowledgeProvider(
        client, ["contoso.sharepoint.com:/sites/Eng"], download=download
    )

    async def main() -> Any:
        try:
            hits = await p.search("It's a runbook")
            md = await p.get(hits[0].id)
            html = await p.get(hits[1].id)
            with pytest.raises(ValidationError, match="Only text documents"):
                await p.get("sharepoint:contoso.sharepoint.com,guid1,guid2/ITEM2")
            return hits, md, html, await p.get("sharepoint:site/bad id!"), await p.status()
        finally:
            await p.aclose()

    hits, md, html, bad, status = run(main())
    assert [h.title for h in hits] == [
        "Runbook.md",
        "Notes.html",
    ] and "abc123secretxyz" not in hits[0].snippet
    assert md and "abc123secretxyz" not in md.content and "Roll back" in md.content
    assert html and "<p>" not in html.content and bad is None and status.detail
    assert downloads == ["https://dl.example/1", "https://dl.example/3"]
    with pytest.raises(ConfigurationError):
        remote.SharePointKnowledgeProvider(client, ["not a site!"])
    with pytest.raises(ConfigurationError):
        run(remote._default_download("http://plain.example/file"))  # TLS is mandatory
    with pytest.raises(AuthenticationError, match="token is missing") as missing_token:
        remote.env_token({}, "SHAREPOINT_TOKEN", "MS_GRAPH_TOKEN")()
    assert "SHAREPOINT_TOKEN" in (missing_token.value.hint or "")


def test_pdf_provider_and_html_and_scoring(tmp_path: Path) -> None:
    docs = tmp_path / "pdfs"
    docs.mkdir()
    (docs / "Deploy_guide.pdf").write_text("Deployment steps and rollback. token=supersecretvalue1")
    (docs / "Broken.pdf").write_text("unreadable")
    (docs / "notes.txt").write_text("deployment deployment")

    class FakePdf(PdfKnowledgeProvider):
        extractor = staticmethod(  # type: ignore[assignment]
            lambda p: (
                (_ for _ in ()).throw(ValidationError("bad pdf"))
                if p.name == "Broken.pdf"
                else p.read_text()
            )
        )

    p = FakePdf([docs], tmp_path / "pdf-manifest.json", clock=lambda: NOW)
    hits = run(p.search("deployment rollback"))
    assert [h.title for h in hits] == ["Deploy guide"] and "supersecretvalue1" not in hits[
        0
    ].snippet
    assert run(p.get("pdfs:Deploy_guide.pdf")).title == "Deploy guide"  # type: ignore[union-attr]
    assert (
        run(p.sync()).documents == 2
    )  # the unreadable PDF is indexed by name but never breaks a search
    (docs / "garbage.pdf").write_bytes(b"not a pdf")
    with pytest.raises(ValidationError, match="Cannot read PDF"):
        extract_pdf_text(docs / "garbage.pdf")
    assert (
        html_to_text(
            "<h1>Title</h1><p>A &amp; B</p><ul><li>x</li><li>y</li></ul><style>p{}</style>"
        )
        == "# Title\nA & B\nx\ny"
    )
    ranked = rank(
        "deploy",
        [("a", "Other", "deploy " * 3), ("b", "Deploy", "unrelated text here")],
        5,
        SecretRedactor(),
    )
    assert [h.id for h in ranked] == ["b", "a"]  # a title match outranks body repetition


def test_knowledge_service_caches_providers_and_closes_clients(tmp_path: Path) -> None:
    require, _ = make_require("viewer")
    built = []
    closed = []

    class Remote:
        name = "r"

        async def search(self, q: str, *, limit: int = 5) -> list[Any]:
            return []

        async def aclose(self) -> None:
            closed.append(1)

    def factory() -> list[Any]:
        built.append(1)
        return [Remote()]

    svc = KnowledgeService(factory, require)

    async def main() -> None:
        await svc.search("a")
        await svc.search("b")
        await svc.aclose()

    run(main())
    assert built == [1] and closed == [1]  # created once, always released


# ================================ Claude subagents and knowledge (M13) ==============================
def make_claude_service(
    tmp_path: Path, *roles: str, **guard_kw: Any
) -> tuple[ClaudeIntegrationService, Path]:
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    require, ident = make_require(*roles)
    guard, *_ = make_guard(**guard_kw)
    svc = ClaudeIntegrationService(
        ClaudeCodeManager(project),
        FilesystemAgentRegistry(project),
        require,
        make_audit(tmp_path, ident),
        guard,
    )
    return svc, project


def test_claude_subagents_least_privilege_and_never_overwrite_developer_files(
    tmp_path: Path,
) -> None:
    svc, project = make_claude_service(tmp_path, "maintainer")
    result = svc.sync_agents("code-review")
    assert result and result[0][1].changed
    text = (project / ".claude/agents/code-review.md").read_text()
    assert text.startswith("---\nname: code-review\n") and AGENT_MARKER in text
    assert "tools: Read, Grep, Glob" in text and "Bash" not in text and "Edit" not in text
    assert svc.sync_agents("code-review")[0][1].changed is False  # type: ignore[index]  # idempotent
    dev_file = project / ".claude/agents/security-review.md"
    dev_file.write_text("---\nname: security-review\n---\nMy own subagent\n")
    with pytest.raises(ValidationError, match="not created by Stratos"):
        svc.sync_agents("security-review")
    assert dev_file.read_text().endswith("My own subagent\n")  # untouched
    assert ("claude.configure", "failure") in audit_results(tmp_path)
    with pytest.raises(ResourceNotFoundError):
        svc.sync_agents("ghost")


def test_claude_subagent_tools_follow_permissions_and_removal_only_for_ours(tmp_path: Path) -> None:
    m = ClaudeCodeManager(tmp_path)
    powerful = AgentDefinition(
        name="fixer", instructions="Fix it", description="Fixes\nthings",
        permissions=(AgentPermission.REPO_WRITE, AgentPermission.RUN_COMMANDS, AgentPermission.NETWORK),
    )  # fmt: skip
    body = m.agent_markdown(powerful)
    assert (
        "tools: Read, Grep, Glob, Edit, Write, Bash, WebFetch" in body
        and "description: Fixes things" in body
    )
    m.sync_agent(powerful)
    assert (
        m.synced_agent_names() == ["fixer"]
        and m.remove_agent("fixer") is True
        and m.synced_agent_names() == []
    )
    (tmp_path / ".claude/agents/mine.md").write_text("mine")
    assert m.remove_agent("mine") is False and (tmp_path / ".claude/agents/mine.md").exists()
    with pytest.raises(ValidationError):
        m.sync_agent(AgentDefinition(name="../evil", instructions="x"))


def test_claude_service_permissions_dry_run_and_knowledge_block(tmp_path: Path) -> None:
    with pytest.raises(AuthorizationError):
        make_claude_service(tmp_path / "a", "developer")[0].sync_agents()
    svc, project = make_claude_service(tmp_path / "b", "maintainer", dry_run=True)
    assert svc.sync_agents() is None and svc.connect_knowledge(["x"]) is None
    assert not (project / ".claude").exists() and not (project / "CLAUDE.md").exists()
    live, project2 = make_claude_service(tmp_path / "c", "maintainer")
    (project2 / "CLAUDE.md").write_text("# Mine\nKeep me.\n")
    live.connect_knowledge(["Markdown folder: docs", "GitHub: acme/docs"])
    text = (project2 / "CLAUDE.md").read_text()
    assert "Keep me." in text and "stratos knowledge search" in text and "GitHub: acme/docs" in text
    live.apply_instructions(organisation="Org rule")
    assert (
        "Org rule" in (project2 / "CLAUDE.md").read_text()
        and "Keep me." in (project2 / "CLAUDE.md").read_text()
    )
    assert live.status().detected in (True, False)


# ================================ diagnostics (M23) =================================================
def good_facts(**over: Any) -> Facts:
    base: dict[str, Any] = dict(
        python=(3, 12), config_error=None, keyring_backend="windows.WinVaultKeyring", auth_configured=True,
        signed_in=True, git_found=True, github_token_found=True, claude_level="pass",
        claude_detail="Claude Code detected.", ai_provider="claude", ai_key_configured=True,
        skills_registry=True, mcp_registry=True, knowledge_sources=1,
    )  # fmt: skip
    return Facts(**{**base, **over})


def test_doctor_checks_pass_warn_and_fail() -> None:
    assert summarise(run_checks(good_facts())) == (12, 0, 0)
    weak = run_checks(
        good_facts(git_found=False, signed_in=False, ai_key_configured=False, knowledge_sources=0)
    )
    assert summarise(weak)[1] == 4 and summarise(weak)[2] == 0
    broken = run_checks(
        good_facts(python=(3, 9), config_error="Invalid configuration: api.endpoint")
    )
    assert summarise(broken)[2] == 2
    assert {c.name: c.level for c in run_checks(good_facts(keyring_backend="fail.Keyring"))}[
        "Secure credential store"
    ] == "warn"
    no_auth = run_checks(good_facts(auth_configured=False, signed_in=None))
    assert "Signed in" not in [c.name for c in no_auth]
    assert {c.name: c.level for c in run_checks(good_facts(claude_level="warning"))}[
        "Claude Code"
    ] == "warn"


def test_cli_doctor_version_status(
    cli_sandbox: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = cli_sandbox("viewer")
    monkeypatch.setenv("PATH", "")
    r = runner.invoke(app, ["-o", "json", "doctor"])
    assert r.exit_code == 0, r.output
    rows = {row["check"]: row for row in json.loads(r.output[: r.output.rindex("]") + 1])}
    assert rows["Python"]["result"] == "PASS" and rows["git"]["result"] == "WARN"
    v = json.loads(runner.invoke(app, ["-o", "json", "version"]).output)
    assert v["stratos"] and v["python"]
    s = json.loads(runner.invoke(app, ["-o", "json", "status"]).output)
    assert s["signed_in"] is True and s["organisation"] == "Stratos-Technologies-fzco"
    (project / "user.yaml").write_text("api:\n  endpoint: not-a-url\n")
    assert runner.invoke(app, ["doctor"]).exit_code == 9  # a failed check is a dependency error


# ================================ governance and monitoring (M09/M10) ================================
def settings_with(**policy: Any) -> Any:
    from stratos.config.settings import Settings

    return Settings(policy=policy)


def test_policy_service_checks_and_summary() -> None:
    p = PolicyService(
        settings_with(
            allow_public_repos=False, allowed_ai_providers=["claude"], allowed_ai_models=["m1"]
        )
    )
    with pytest.raises(AuthorizationError, match="Public repositories"):
        p.check_public_repo()
    p.check_provider("Claude")
    with pytest.raises(AuthorizationError, match="openai"):
        p.check_provider("openai")
    p.check_model("m1")
    p.check_model(None)
    with pytest.raises(AuthorizationError, match="m2"):
        p.check_model("m2")
    open_policy = PolicyService(settings_with())
    open_policy.check_public_repo()
    open_policy.check_provider("anything")
    open_policy.check_model("any-model")
    assert open_policy.summary()["stratos.policy.allowed_ai_models"] == "any"
    assert p.summary()["stratos.policy.allow_public_repos"] is False


def test_public_repo_policy_blocks_creation_and_audits_denial(tmp_path: Path) -> None:
    svc, fake, *_ = make_repo_service(tmp_path, "developer", yes=True)
    svc._policy = PolicyService(settings_with(allow_public_repos=False))
    with pytest.raises(AuthorizationError, match="organisation policy"):
        svc.create("api", private=False)
    assert fake.created == 0 and audit_results(tmp_path) == [("repo.create", "denied")]
    assert svc.create("api", private=True).created  # type: ignore[union-attr]  # private is still fine


def test_ai_and_agent_model_policy(tmp_path: Path) -> None:
    require, _ = make_require("viewer")
    policy = PolicyService(
        settings_with(allowed_ai_models=["approved-model"], allowed_ai_providers=["fake"])
    )
    fake = FakeProvider()
    svc = AIService(
        lambda: fake, require, provider_name="fake", default_model="approved-model", max_tokens=9,
        api_key_configured=True, check_provider=policy.check_provider, check_model=policy.check_model,
    )  # fmt: skip
    assert run(svc.ask("q")).model == "approved-model"
    with pytest.raises(AuthorizationError, match="rogue-model"):
        run(svc.ask("q", model="rogue-model"))
    wrong_provider = AIService(
        lambda: fake, require, provider_name="openai", default_model="approved-model", max_tokens=9,
        api_key_configured=True, check_provider=policy.check_provider,
    )  # fmt: skip
    with pytest.raises(AuthorizationError, match="openai"):
        run(wrong_provider.ask("q"))
    assert len(fake.prompts) == 1  # blocked calls never reached the provider


def test_audit_summary_analytics_and_alerts(tmp_path: Path) -> None:
    require, ident = make_require("maintainer")
    store = LocalAuditStore(tmp_path / "audit.jsonl")
    now = datetime(2026, 9, 21, tzinfo=UTC)
    from stratos.application.audit_service import AuditService

    cursor = {"t": now - timedelta(days=10)}
    users = {"who": "ann@x.io"}
    identity = lambda: type(ident)(subject="s", email=users["who"], organisation="acme")  # noqa: E731
    audit = AuditService(store, identity, clock=lambda: cursor["t"])
    audit.emit(
        AuditAction.REPO_CREATE, "repo", "old", result=AuditResult.SUCCESS
    )  # outside the window
    cursor["t"] = now - timedelta(days=1)
    for _ in range(3):
        audit.emit(AuditAction.REPO_CREATE, "repo", "r", result=AuditResult.SUCCESS)
    users["who"] = "eve@x.io"
    for _ in range(5):
        audit.emit(AuditAction.PROJECT_DELETE, "project", "p", result=AuditResult.DENIED)
    audit.emit(AuditAction.AGENT_RUN, "agent", "a", result=AuditResult.FAILURE)
    audit.emit(AuditAction.AGENT_RUN, "agent", "a", result=AuditResult.FAILURE)
    summary = AuditService(store, identity, clock=lambda: now).summary(days=7)
    assert summary.total == 10 and summary.by_result == {"success": 3, "denied": 5, "failure": 2}
    assert summary.by_user == {"ann@x.io": 3, "eve@x.io": 7} and summary.failure_rate == 0.2
    assert summary.by_action["project.delete"] == 5
    assert any("5 denied actions" in a for a in summary.alerts) and any(
        "eve@x.io was denied 5" in a for a in summary.alerts
    )
    assert not any("failure rate" in a for a in summary.alerts)  # 20% is below the 25% threshold
    strict = AuditService(store, identity, clock=lambda: now).summary(
        days=7, failure_rate_threshold=0.1
    )
    assert any("failure rate 20%" in a for a in strict.alerts)
    quiet = AuditService(store, identity, clock=lambda: now).summary(
        days=1, denied_threshold=99, failure_rate_threshold=0.9
    )
    assert quiet.alerts == () and quiet.total == 10
    empty = AuditService(
        LocalAuditStore(tmp_path / "none.jsonl"), identity, clock=lambda: now
    ).summary(days=7)
    assert empty.total == 0 and empty.failure_rate == 0.0 and empty.alerts == ()


def test_cli_audit_summary_and_policy_config(
    cli_sandbox: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = cli_sandbox("maintainer")
    from stratos.application.audit_service import AuditService
    from stratos.cli import context

    store = LocalAuditStore(project / "audit.jsonl")
    who = type(make_require("viewer")[1])(subject="s", email="eve@x.io")
    audit = AuditService(store, lambda: who)
    for _ in range(6):
        audit.emit(AuditAction.REPO_DELETE, "repo", "x", result=AuditResult.DENIED)
    r = runner.invoke(app, ["-o", "json", "audit", "summary", "--days", "1"])
    assert r.exit_code == 0 and "ALERT" in r.output and '"events": 6' in r.output
    assert (
        runner.invoke(app, ["config", "set", "policy.allow_public_repos", "false"]).exit_code == 0
    )
    assert (
        runner.invoke(app, ["config", "set", "policy.allowed_ai_models", "m1, m2"]).exit_code == 0
    )
    assert runner.invoke(app, ["config", "set", "monitoring.denied_threshold", "2"]).exit_code == 0
    cfg = json.loads(
        runner.invoke(app, ["-o", "json", "config", "get", "policy.allowed_ai_models"]).output
    )
    assert cfg["policy.allowed_ai_models"] == ["m1", "m2"]
    fake = RichFake()
    monkeypatch.setattr(context.CliContext, "github", property(lambda self: fake), raising=False)
    denied = runner.invoke(app, ["repo", "create", "api", "--public", "--yes"])
    assert denied.exit_code == 4 and fake.created == 0
    runner.invoke(app, ["config", "set", "auth.client_id", "cid"])
    pol = json.loads(runner.invoke(app, ["-o", "json", "org", "policy"]).output)
    assert pol["stratos.policy.allow_public_repos"] is False and pol[
        "stratos.policy.allowed_ai_models"
    ] == ["m1", "m2"]
    assert runner.invoke(app, ["config", "set", "monitoring.denied_threshold", "x"]).exit_code == 8


def test_cli_agent_sync_and_knowledge_connect(cli_sandbox: Callable[..., Path]) -> None:
    project = cli_sandbox("maintainer")
    r = runner.invoke(app, ["agent", "sync", "--dry-run"])
    assert r.exit_code == 0 and "DRY RUN" in r.output and not (project / ".claude").exists()
    assert runner.invoke(app, ["agent", "sync", "code-review"]).exit_code == 0
    assert (project / ".claude/agents/code-review.md").exists()
    assert runner.invoke(app, ["agent", "sync", "ghost"]).exit_code == 5
    make_docs(project / "docs")
    runner.invoke(app, ["config", "set", "knowledge.paths", "docs"])
    assert runner.invoke(app, ["knowledge", "connect"]).exit_code == 0
    text = (project / "CLAUDE.md").read_text()
    assert "stratos knowledge search" in text and "Markdown folder: docs" in text


def test_cli_knowledge_many_sources_and_no_sources_hint(
    cli_sandbox: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = cli_sandbox("viewer")
    make_docs(project / "docs")
    (project / "pdfs").mkdir()
    runner.invoke(app, ["config", "set", "knowledge.paths", "docs"])
    runner.invoke(app, ["config", "set", "knowledge.pdf_paths", "pdfs"])
    rows = json.loads(runner.invoke(app, ["-o", "json", "knowledge", "status"]).output)
    assert {row["provider"] for row in rows} == {"markdown", "pdf"}
    runner.invoke(app, ["config", "reset"])
    runner.invoke(app, ["config", "set", "auth.issuer", "https://idp.example.com"])
    runner.invoke(app, ["config", "set", "auth.client_id", "cid"])
    cli_sandbox("viewer")
    r = runner.invoke(app, ["knowledge", "status"])
    flat = r.output.replace("\n", "").replace(" ", "")
    assert r.exit_code == 8 and "knowledge.confluence_url" in flat


# ================================ CI, packaging and docs ====================================================
def test_ci_workflows_cover_supply_chain_controls() -> None:
    root = Path(__file__).resolve().parents[2] / ".github" / "workflows"
    ci = yaml.safe_load((root / "ci.yml").read_text(encoding="utf-8"))
    security = yaml.safe_load((root / "security.yml").read_text(encoding="utf-8"))
    release = yaml.safe_load((root / "release.yml").read_text(encoding="utf-8"))
    assert ci["permissions"] == {"contents": "read"} and "test" in ci["jobs"]
    text = (root / "security.yml").read_text(encoding="utf-8")
    assert set(security["jobs"]) == {
        "dependency-audit",
        "dependency-review",
        "sast",
        "secret-scan",
        "sbom",
    }
    assert (
        "pip_audit" in text
        and "codeql-action" in text
        and "gitleaks" in text
        and "cyclonedx" in text
    )
    rel = (root / "release.yml").read_text(encoding="utf-8")
    assert (
        "attest-build-provenance" in rel
        and "cyclonedx" in rel
        and set(release["jobs"]) == {"build", "release", "pypi"}
    )
    assert release["permissions"] == {
        "contents": "read"
    }  # elevated rights only on the jobs that need them


def test_packaging_metadata_declares_optional_extras() -> None:
    import tomllib

    data = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert set(data["project"]["optional-dependencies"]) == {"openai", "pdf"}
    assert data["project"]["scripts"]["stratos"] == "stratos.cli.app:app"


def test_settings_defaults_for_new_sections() -> None:
    s = load_settings(
        {}, env={}, org_path=Path("nope1"), user_path=Path("nope2"), project_path=Path("nope3")
    )
    assert s.policy.allow_public_repos is True and s.policy.allowed_ai_models is None
    assert s.monitoring.denied_threshold == 5 and s.agents.max_steps == 6
    assert s.knowledge.github == [] and s.ai.azure_api_version
