"""Role-based access control. Least privilege: unknown or missing roles grant only `viewer`.

The role-to-permission matrix below is a proposed default; organisation policy (Phase 12)
may replace it.
"""

from collections.abc import Mapping

from stratos.domain.enums import Permission, Role
from stratos.domain.exceptions import AuthorizationError
from stratos.domain.models.auth import Identity

P = Permission

_VIEWER = frozenset({P.ORG_READ})
_DEVELOPER = _VIEWER | {
    P.PROJECT_CREATE,
    P.REPO_CREATE,
    P.AGENT_RUN,
    P.SKILL_INSTALL,
    P.DEPLOYMENT_CREATE,
}
_MAINTAINER = _DEVELOPER | {
    P.MCP_INSTALL,
    P.MCP_CONFIGURE,
    P.PROJECT_DELETE,
    P.DEPLOYMENT_ROLLBACK,
    P.AUDIT_READ,
    P.REPO_CONFIGURE,
    P.REPO_ARCHIVE,
    P.SKILL_REMOVE,
    P.AGENT_CREATE,
    P.CLAUDE_CONFIGURE,
}
_ADMIN = frozenset(Permission)

DEFAULT_MATRIX: Mapping[Role, frozenset[Permission]] = {
    Role.VIEWER: _VIEWER,
    Role.DEVELOPER: _DEVELOPER,
    Role.MAINTAINER: _MAINTAINER,
    Role.ADMIN: _ADMIN,
}


class AuthorizationService:
    def __init__(self, matrix: Mapping[Role, frozenset[Permission]] = DEFAULT_MATRIX) -> None:
        self._matrix = matrix

    def roles_for(self, identity: Identity) -> frozenset[Role]:
        known = {Role(r.strip().lower()) for r in identity.roles if r.strip().lower() in Role}
        return frozenset(known) or frozenset({Role.VIEWER})

    def permissions_for(self, identity: Identity) -> frozenset[Permission]:
        return frozenset().union(*(self._matrix[r] for r in self.roles_for(identity)))

    def is_allowed(self, identity: Identity, permission: Permission) -> bool:
        return permission in self.permissions_for(identity)

    def require(self, identity: Identity, permission: Permission) -> None:
        if not self.is_allowed(identity, permission):
            raise AuthorizationError(
                f"You do not have permission for '{permission.value}'.",
                hint="Ask an organisation administrator to grant the required role.",
            )
