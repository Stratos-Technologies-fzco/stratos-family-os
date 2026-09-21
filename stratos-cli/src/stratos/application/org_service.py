"""Organisation and team read access (metadata cached; only non-sensitive data is cached)."""

from collections.abc import Callable
from typing import Any

from stratos.domain.enums import Permission
from stratos.domain.exceptions import ResourceNotFoundError
from stratos.domain.interfaces.github import GitHubPort
from stratos.domain.models.auth import Identity
from stratos.domain.models.github import Member, OrgInfo, Team, TeamRepoPermission
from stratos.infrastructure.filesystem.cache import TtlCache
from stratos.utils.validation import validate_name

Require = Callable[[Permission], Identity]
TTL = 600.0  # seconds


class OrgService:
    def __init__(
        self,
        github: GitHubPort,
        require: Require,
        cache: TtlCache,
        default_org: str,
        local_policy: Callable[[], dict[str, Any]] = dict,
    ) -> None:
        self._gh = github
        self._require = require
        self._cache = cache
        self._org = default_org
        self._local_policy = local_policy

    def _name(self, org: str | None) -> str:
        return validate_name(org or self._org, "organisation")

    def get(self, org: str | None = None, *, refresh: bool = False) -> OrgInfo:
        org = self._name(org)
        self._require(Permission.ORG_READ)
        if refresh:
            self._cache.invalidate("org", org)
        data = self._cache.get_or_load(
            "org", org, TTL, lambda: self._gh.get_org(org).model_dump(mode="json")
        )
        return OrgInfo.model_validate(data)

    def members(self, org: str | None = None) -> list[Member]:
        org = self._name(org)
        self._require(Permission.ORG_READ)
        data = self._cache.get_or_load(
            "org-members", org, TTL, lambda: [m.model_dump() for m in self._gh.list_members(org)]
        )
        return [Member.model_validate(m) for m in data]

    def teams(self, org: str | None = None) -> list[Team]:
        org = self._name(org)
        self._require(Permission.ORG_READ)
        data = self._cache.get_or_load(
            "org-teams", org, TTL, lambda: [t.model_dump() for t in self._gh.list_teams(org)]
        )
        return [Team.model_validate(t) for t in data]

    def policy(self, org: str | None = None) -> dict[str, Any]:
        """Policy view: GitHub organisation settings plus Stratos policy from configuration."""
        info = self.get(org)
        return {
            "github.two_factor_required": info.two_factor_requirement_enabled,
            "github.members_can_create_repositories": info.members_can_create_repositories,
            "github.default_repository_permission": info.default_repository_permission,
            **self._local_policy(),
        }


class TeamService:
    def __init__(
        self, github: GitHubPort, require: Require, cache: TtlCache, default_org: str
    ) -> None:
        self._gh = github
        self._require = require
        self._cache = cache
        self._org = default_org

    def _name(self, org: str | None) -> str:
        return validate_name(org or self._org, "organisation")

    def list_teams(self, org: str | None = None) -> list[Team]:
        org = self._name(org)
        self._require(Permission.ORG_READ)
        data = self._cache.get_or_load(
            "org-teams", org, TTL, lambda: [t.model_dump() for t in self._gh.list_teams(org)]
        )
        return [Team.model_validate(t) for t in data]

    def get(self, slug: str, org: str | None = None) -> Team:
        org = self._name(org)
        self._require(Permission.ORG_READ)
        team = self._gh.get_team(org, validate_name(slug, "team"))
        if team is None:
            raise ResourceNotFoundError(f"Team '{slug}' was not found in {org}.")
        return team

    def members(self, slug: str, org: str | None = None) -> list[Member]:
        org = self._name(org)
        self._require(Permission.ORG_READ)
        self.get(slug, org)
        return self._gh.team_members(org, slug)

    def permissions(self, slug: str, org: str | None = None) -> list[TeamRepoPermission]:
        org = self._name(org)
        self._require(Permission.ORG_READ)
        self.get(slug, org)
        return self._gh.team_repos(org, slug)
