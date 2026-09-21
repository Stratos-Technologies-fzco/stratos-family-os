"""Shared fakes for the Layer D tests: a GitHub with files, environments and deployments."""

import subprocess
from pathlib import Path
from typing import Any

from test_gaps import RichFake

from stratos.domain.exceptions import APIError, ResourceNotFoundError
from stratos.domain.models.github import Repository
from stratos.domain.models.workflow import DeploymentInfo, DeploymentStatus, EnvironmentInfo

SHA_MAIN = "a" * 40
SHA_V1 = "b" * 40
SHA_V2 = "c" * 40


class DevFake(RichFake):
    def __init__(self) -> None:
        super().__init__()
        self.files: dict[tuple[str, str], bytes] = {}
        self.envs: dict[str, dict[str, Any]] = {}
        self.deployments: list[DeploymentInfo] = []
        self.statuses: dict[int, list[DeploymentStatus]] = {}
        self.refs: dict[str, str] = {"main": SHA_MAIN, "v1": SHA_V1, "v2": SHA_V2}
        self.fail: dict[str, int] = {}  # method name -> number of upcoming failures
        self.calls: list[str] = []
        self.descriptions: dict[str, str] = {}
        self._next_id = 100

    def _maybe_fail(self, name: str) -> None:
        self.calls.append(name)
        if self.fail.get(name, 0) > 0:
            self.fail[name] -= 1
            raise APIError("GitHub exploded token=abc123secretxyz")

    # ---- repositories --------------------------------------------------------------------
    def create_repo(
        self, org: str, name: str, *, private: bool = True, description: str | None = None
    ) -> Repository:
        self._maybe_fail("create_repo")
        return super().create_repo(org, name, private=private, description=description)

    def protect_branch(self, org: str, repo: str, branch: str) -> None:
        self._maybe_fail("protect_branch")
        super().protect_branch(org, repo, branch)

    def apply_security(self, org: str, repo: str) -> list[str]:
        self._maybe_fail("apply_security")
        return super().apply_security(org, repo)

    def update_repo(self, org: str, name: str, *, description: str) -> Repository:
        self.descriptions[f"{org}/{name}"] = description
        return self.repos[f"{org}/{name}"]

    # ---- files ---------------------------------------------------------------------------
    def get_file(self, org: str, repo: str, path: str, *, ref: str | None = None) -> str | None:
        data = self.files.get((repo, path))
        return None if data is None else data.decode("utf-8", errors="replace")

    def put_file(
        self, org: str, repo: str, path: str, content: str | bytes, *, branch: str, message: str
    ) -> bool:
        self._maybe_fail("put_file")
        data = content.encode("utf-8") if isinstance(content, str) else content
        if self.files.get((repo, path)) == data:
            return False
        self.files[(repo, path)] = data
        return True

    def text(self, repo: str, path: str) -> str:
        return self.files[(repo, path)].decode("utf-8")

    # ---- environments --------------------------------------------------------------------
    def create_environment(
        self, org: str, repo: str, name: str, *, protected: bool = False
    ) -> EnvironmentInfo:
        self._maybe_fail("create_environment")
        self.envs[name] = {"protected": protected}
        return EnvironmentInfo(name=name, protected=protected)

    def list_environments(self, org: str, repo: str) -> list[EnvironmentInfo]:
        return [EnvironmentInfo(name=n, protected=e["protected"]) for n, e in self.envs.items()]

    def get_environment(self, org: str, repo: str, name: str) -> EnvironmentInfo | None:
        e = self.envs.get(name)
        return None if e is None else EnvironmentInfo(name=name, protected=e["protected"])

    def delete_environment(self, org: str, repo: str, name: str) -> None:
        self.envs.pop(name, None)

    # ---- deployments ---------------------------------------------------------------------
    def resolve_ref(self, org: str, repo: str, ref: str) -> str:
        if ref in self.refs:
            return self.refs[ref]
        if len(ref) == 40 and all(c in "0123456789abcdef" for c in ref):
            return ref
        raise ResourceNotFoundError(f"Ref '{ref}' was not found.")

    def create_deployment(
        self,
        org: str,
        repo: str,
        *,
        ref: str,
        environment: str,
        description: str = "",
        payload: dict[str, object] | None = None,
    ) -> DeploymentInfo:
        self._maybe_fail("create_deployment")
        self._next_id += 1
        info = DeploymentInfo(
            id=self._next_id, ref=ref, sha=ref, environment=environment,
            creator="dev", description=description, payload=payload or {},
        )  # fmt: skip
        self.deployments.insert(0, info)
        self.statuses[info.id] = []
        return info

    def list_deployments(
        self, org: str, repo: str, *, environment: str | None = None, limit: int = 10
    ) -> list[DeploymentInfo]:
        found = [d for d in self.deployments if environment is None or d.environment == environment]
        return found[:limit]

    def deployment_statuses(
        self, org: str, repo: str, deployment_id: int
    ) -> list[DeploymentStatus]:
        return list(self.statuses.get(deployment_id, []))

    def add_deployment_status(
        self, org: str, repo: str, deployment_id: int, state: str, *, description: str = ""
    ) -> None:
        self.statuses.setdefault(deployment_id, []).insert(
            0, DeploymentStatus(state=state, description=description)
        )

    def finish(self, deployment_id: int, state: str) -> None:
        """Simulate the CI pipeline posting a status."""
        self.add_deployment_status("acme", "x", deployment_id, state)


def git_clone_fake(remote: str) -> Any:
    """A clone function that makes a real (empty) git repository with the given origin."""

    def clone(org: str, name: str, dest: Path) -> Path:
        dest.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(dest)], check=True)
        subprocess.run(["git", "-C", str(dest), "remote", "add", "origin", remote], check=True)
        (dest / ".env.example").write_text("API_URL=https://example.test\nAPI_TOKEN=\n")
        (dest / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        wf = dest / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text("name: CI\n")
        return dest

    return clone


def init_adapters() -> dict[str, Any]:
    """The adapter factories `InitService` is given (normally built in cli/wiring)."""
    from stratos.infrastructure.claude.manager import ClaudeCodeManager
    from stratos.infrastructure.filesystem.config_files import ConfigFileProtector

    return {
        "protector_factory": lambda dry: ConfigFileProtector(dry_run=dry),
        "claude_factory": lambda folder, protector: ClaudeCodeManager(folder, protector),
    }
