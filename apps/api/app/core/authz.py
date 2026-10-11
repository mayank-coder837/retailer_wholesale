"""What a member may do, and where (M0.3 plan → Scope rules).

A grant pairs a permission with a scope: the whole tenant, one business, or one branch of that business.
This module is pure logic; ``app.modules.identity.authz`` loads a member's grants from the database.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from http import HTTPStatus

from app.core.errors import DomainError


class PermissionDeniedError(DomainError):
    code = "permission_denied"
    status = HTTPStatus.FORBIDDEN
    title = "You do not have permission for this"


class MembershipInactiveError(DomainError):
    code = "membership_inactive"
    status = HTTPStatus.FORBIDDEN
    title = "Your access to this account is suspended"


@dataclass(frozen=True, slots=True)
class Scope:
    business_id: uuid.UUID | None = None
    branch_id: uuid.UUID | None = None

    @property
    def tenant_wide(self) -> bool:
        return self.business_id is None

    def covers(self, business_id: uuid.UUID | None, branch_id: uuid.UUID | None) -> bool:
        """True when acting *on* (business, branch) is inside this scope.

        ``business_id=None`` means a tenant-level action (only tenant-wide scopes cover it);
        ``branch_id=None`` means a business-level action (branch scopes do not cover it).
        """
        if self.business_id is not None and self.business_id != business_id:
            return False
        return self.branch_id is None or self.branch_id == branch_id


TENANT_WIDE = Scope()


@dataclass(frozen=True)
class PermissionSet:
    grants: dict[str, frozenset[Scope]] = field(default_factory=dict)

    @classmethod
    def from_grants(cls, rows: Iterable[tuple[str, Scope]]) -> "PermissionSet":
        collected: dict[str, set[Scope]] = {}
        for code, scope in rows:
            collected.setdefault(code, set()).add(scope)
        return cls({code: frozenset(scopes) for code, scopes in collected.items()})

    def holds(self, code: str) -> bool:
        """Holds the permission somewhere (any scope)."""
        return code in self.grants

    def allows(
        self, code: str, business_id: uuid.UUID | None = None, branch_id: uuid.UUID | None = None
    ) -> bool:
        return any(scope.covers(business_id, branch_id) for scope in self.grants.get(code, ()))

    def can_see(self, code: str, business_id: uuid.UUID) -> bool:
        """Viewing a container: a grant anywhere inside the business is enough to see the business itself."""
        return any(s.business_id in (None, business_id) for s in self.grants.get(code, ()))

    def require(
        self, code: str, business_id: uuid.UUID | None = None, branch_id: uuid.UUID | None = None
    ) -> None:
        if not self.allows(code, business_id, branch_id):
            raise PermissionDeniedError(permission=code)

    def require_see(self, code: str, business_id: uuid.UUID) -> None:
        if not self.can_see(code, business_id):
            raise PermissionDeniedError(permission=code)

    def require_held(self, code: str) -> None:
        if not self.holds(code):
            raise PermissionDeniedError(permission=code)
