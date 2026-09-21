"""GitHub REST adapter built on the shared API client (TLS, retries, rate limits, request IDs)."""

import base64
from typing import Any
from urllib.parse import quote

from stratos.domain.exceptions import ResourceNotFoundError, ValidationError
from stratos.domain.models.github import (
    Branch,
    Issue,
    Label,
    Member,
    OrgInfo,
    PullRequest,
    Repository,
    Team,
    TeamRepoPermission,
    Workflow,
    WorkflowRun,
)
from stratos.infrastructure.api.client import PlatformApiClient
from stratos.utils.validation import validate_name

GITHUB_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
_ROLE_ORDER = ("admin", "maintain", "push", "triage", "pull")

PROTECTION = {
    "required_status_checks": None,
    "enforce_admins": True,
    "required_pull_request_reviews": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews": True,
        "require_code_owner_reviews": True,
    },
    "restrictions": None,
    "allow_force_pushes": False,
    "allow_deletions": False,
    "required_conversation_resolution": True,
}


def _repo(d: dict[str, Any]) -> Repository:
    return Repository(
        name=d["name"],
        full_name=d["full_name"],
        private=d.get("private", True),
        default_branch=d.get("default_branch") or "main",
        description=d.get("description"),
        url=d.get("html_url", ""),
        archived=d.get("archived", False),
    )


class GitHubService:
    """Implements GitHubPort. All names are validated before they reach a URL path."""

    def __init__(self, client: PlatformApiClient) -> None:
        self._c = client

    # ---- repositories --------------------------------------------------------------------
    def get_repo(self, org: str, name: str) -> Repository | None:
        validate_name(org, "organisation"), validate_name(name, "repository name")
        try:
            return _repo(self._c.get(f"/repos/{org}/{name}"))
        except ResourceNotFoundError:
            return None

    def create_repo(
        self, org: str, name: str, *, private: bool = True, description: str | None = None
    ) -> Repository:
        validate_name(org, "organisation"), validate_name(name, "repository name")
        body: dict[str, Any] = {"name": name, "private": private, "auto_init": True}
        if description:
            body["description"] = description
        return _repo(self._c.post(f"/orgs/{org}/repos", json=body))

    def list_repos(self, org: str, *, limit: int = 100) -> list[Repository]:
        validate_name(org, "organisation")
        items = self._c.paginate(
            f"/orgs/{org}/repos", {"per_page": 100, "type": "all"}, limit=limit
        )
        return [_repo(d) for d in items]

    def archive_repo(self, org: str, name: str) -> Repository:
        validate_name(org, "organisation"), validate_name(name, "repository name")
        return _repo(self._c.request("PATCH", f"/repos/{org}/{name}", json={"archived": True}))

    def protect_branch(self, org: str, repo: str, branch: str) -> None:
        validate_name(org, "organisation"), validate_name(repo, "repository name")
        self._c.put(f"/repos/{org}/{repo}/branches/{_branch(branch)}/protection", json=PROTECTION)

    def put_codeowners(self, org: str, repo: str, content: str, branch: str) -> bool:
        validate_name(org, "organisation"), validate_name(repo, "repository name")
        path = f"/repos/{org}/{repo}/contents/.github/CODEOWNERS"
        sha: str | None = None
        try:
            current = self._c.get(path, params={"ref": branch})
            if base64.b64decode(current.get("content", "")).decode("utf-8") == content:
                return False
            sha = current.get("sha")
        except ResourceNotFoundError:
            pass
        body: dict[str, Any] = {
            "message": "chore: configure CODEOWNERS",
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if sha:
            body["sha"] = sha
        self._c.put(path, json=body)
        return True

    # ---- organisation and teams ----------------------------------------------------------
    def get_org(self, org: str) -> OrgInfo:
        validate_name(org, "organisation")
        d = self._c.get(f"/orgs/{org}")
        return OrgInfo(
            login=d["login"],
            name=d.get("name"),
            description=d.get("description"),
            members_can_create_repositories=d.get("members_can_create_repositories"),
            default_repository_permission=d.get("default_repository_permission"),
            two_factor_requirement_enabled=d.get("two_factor_requirement_enabled"),
        )

    def list_members(self, org: str, *, limit: int = 200) -> list[Member]:
        validate_name(org, "organisation")
        admins = {
            m["login"]
            for m in self._c.paginate(f"/orgs/{org}/members", {"per_page": 100, "role": "admin"})
        }
        return [
            Member(login=m["login"], role="admin" if m["login"] in admins else "member")
            for m in self._c.paginate(f"/orgs/{org}/members", {"per_page": 100}, limit=limit)
        ]

    @staticmethod
    def _team(d: dict[str, Any]) -> Team:
        return Team(
            slug=d["slug"],
            name=d["name"],
            description=d.get("description"),
            privacy=d.get("privacy"),
            permission=d.get("permission"),
        )

    def list_teams(self, org: str, *, limit: int = 200) -> list[Team]:
        validate_name(org, "organisation")
        return [
            self._team(d)
            for d in self._c.paginate(f"/orgs/{org}/teams", {"per_page": 100}, limit=limit)
        ]

    def get_team(self, org: str, slug: str) -> Team | None:
        validate_name(org, "organisation"), validate_name(slug, "team")
        try:
            return self._team(self._c.get(f"/orgs/{org}/teams/{slug}"))
        except ResourceNotFoundError:
            return None

    def team_members(self, org: str, slug: str, *, limit: int = 200) -> list[Member]:
        validate_name(org, "organisation"), validate_name(slug, "team")
        items = self._c.paginate(
            f"/orgs/{org}/teams/{slug}/members", {"per_page": 100}, limit=limit
        )
        return [Member(login=m["login"]) for m in items]

    def team_repos(self, org: str, slug: str, *, limit: int = 200) -> list[TeamRepoPermission]:
        validate_name(org, "organisation"), validate_name(slug, "team")
        out: list[TeamRepoPermission] = []
        for d in self._c.paginate(
            f"/orgs/{org}/teams/{slug}/repos", {"per_page": 100}, limit=limit
        ):
            perms = d.get("permissions") or {}
            role = d.get("role_name") or next((r for r in _ROLE_ORDER if perms.get(r)), "none")
            out.append(TeamRepoPermission(repository=d["full_name"], permission=role))
        return out

    # ---- branches, pull requests, issues -------------------------------------------------
    def list_branches(self, org: str, repo: str, *, limit: int = 100) -> list[Branch]:
        self._check(org, repo)
        items = self._c.paginate(f"/repos/{org}/{repo}/branches", {"per_page": 100}, limit=limit)
        return [Branch(name=d["name"], protected=bool(d.get("protected"))) for d in items]

    def list_pull_requests(
        self, org: str, repo: str, *, state: str = "open", limit: int = 50
    ) -> list[PullRequest]:
        self._check(org, repo)
        params = {"per_page": 100, "state": _state(state)}
        return [
            PullRequest(
                number=d["number"],
                title=d["title"],
                state=d["state"],
                author=(d.get("user") or {}).get("login", ""),
                head=(d.get("head") or {}).get("ref", ""),
                base=(d.get("base") or {}).get("ref", ""),
                draft=bool(d.get("draft")),
                url=d.get("html_url", ""),
            )
            for d in self._c.paginate(f"/repos/{org}/{repo}/pulls", params, limit=limit)
        ]

    def list_issues(
        self, org: str, repo: str, *, state: str = "open", limit: int = 50
    ) -> list[Issue]:
        self._check(org, repo)
        params = {"per_page": 100, "state": _state(state)}
        issues: list[Issue] = []
        for d in self._c.paginate(f"/repos/{org}/{repo}/issues", params, limit=limit * 2):
            if "pull_request" in d:  # the issues API also returns pull requests
                continue
            issues.append(
                Issue(
                    number=d["number"],
                    title=d["title"],
                    state=d["state"],
                    author=(d.get("user") or {}).get("login", ""),
                    labels=tuple(lb["name"] for lb in d.get("labels", []) if isinstance(lb, dict)),
                    url=d.get("html_url", ""),
                )
            )
        return issues[:limit]

    # ---- labels --------------------------------------------------------------------------
    @staticmethod
    def _label(d: dict[str, Any]) -> Label:
        return Label(
            name=d["name"],
            color=(d.get("color") or "ededed").lower(),
            description=d.get("description") or "",
        )

    def list_labels(self, org: str, repo: str) -> list[Label]:
        self._check(org, repo)
        return [
            self._label(d)
            for d in self._c.paginate(f"/repos/{org}/{repo}/labels", {"per_page": 100})
        ]

    def upsert_labels(self, org: str, repo: str, desired: list[Label]) -> tuple[int, int]:
        existing = {lb.name.lower(): lb for lb in self.list_labels(org, repo)}
        created = updated = 0
        for label in desired:
            body = {"color": label.color, "description": label.description}
            current = existing.get(label.name.lower())
            if current is None:
                self._c.post(f"/repos/{org}/{repo}/labels", json={"name": label.name, **body})
                created += 1
            elif (current.color, current.description) != (label.color, label.description):
                self._c.request(
                    "PATCH", f"/repos/{org}/{repo}/labels/{quote(current.name, safe='')}", json=body
                )
                updated += 1
        return created, updated

    # ---- GitHub Actions ------------------------------------------------------------------
    def list_workflows(self, org: str, repo: str) -> list[Workflow]:
        self._check(org, repo)
        data = self._c.get(f"/repos/{org}/{repo}/actions/workflows", params={"per_page": 100})
        return [
            Workflow(id=w["id"], name=w["name"], state=w["state"], path=w.get("path", ""))
            for w in data.get("workflows", [])
        ]

    def list_workflow_runs(self, org: str, repo: str, *, limit: int = 20) -> list[WorkflowRun]:
        self._check(org, repo)
        data = self._c.get(
            f"/repos/{org}/{repo}/actions/runs", params={"per_page": min(limit, 100)}
        )
        return [
            WorkflowRun(
                id=r["id"],
                name=r.get("name") or "",
                status=r["status"],
                conclusion=r.get("conclusion"),
                branch=r.get("head_branch") or "",
                url=r.get("html_url", ""),
            )
            for r in data.get("workflow_runs", [])[:limit]
        ]

    def configure_actions(self, org: str, repo: str, *, allowed_actions: str = "selected") -> None:
        self._check(org, repo)
        if allowed_actions not in {"all", "local_only", "selected"}:
            raise ValidationError(f"Invalid allowed_actions '{allowed_actions}'.")
        self._c.put(
            f"/repos/{org}/{repo}/actions/permissions",
            json={"enabled": True, "allowed_actions": allowed_actions},
        )

    # ---- repository policy and security --------------------------------------------------
    def apply_repo_policy(self, org: str, repo: str) -> None:
        self._check(org, repo)
        self._c.request(
            "PATCH",
            f"/repos/{org}/{repo}",
            json={
                "allow_squash_merge": True,
                "allow_merge_commit": False,
                "allow_rebase_merge": False,
                "delete_branch_on_merge": True,
                "allow_auto_merge": False,
            },
        )

    def apply_security(self, org: str, repo: str) -> list[str]:
        self._check(org, repo)
        steps: list[tuple[str, str, str, dict[str, Any] | None]] = [
            (
                "secret scanning and push protection",
                "PATCH",
                f"/repos/{org}/{repo}",
                {
                    "security_and_analysis": {
                        "secret_scanning": {"status": "enabled"},
                        "secret_scanning_push_protection": {"status": "enabled"},
                    }
                },
            ),
            ("vulnerability alerts", "PUT", f"/repos/{org}/{repo}/vulnerability-alerts", None),
            (
                "automated security updates",
                "PUT",
                f"/repos/{org}/{repo}/automated-security-fixes",
                None,
            ),
        ]
        results: list[str] = []
        for label, method, path, body in steps:
            try:
                self._c.request(method, path, json=body)
                results.append(f"{label}: enabled")
            except (ValidationError, ResourceNotFoundError):
                results.append(f"{label}: unavailable for this repository or plan")
        return results

    @staticmethod
    def _check(org: str, repo: str) -> None:
        validate_name(org, "organisation")
        validate_name(repo, "repository name")


def _state(state: str) -> str:
    if state not in {"open", "closed", "all"}:
        raise ValidationError(f"Invalid state '{state}'.", hint="Use open, closed or all.")
    return state


def _branch(branch: str) -> str:
    if not branch or ".." in branch or branch.startswith(("/", "-")) or " " in branch:
        raise ValidationError(f"Invalid branch name '{branch[:60]}'.")
    return branch.replace("/", "%2F")
