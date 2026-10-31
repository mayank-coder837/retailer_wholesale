"""Request dependencies: the verified caller and a tenant-scoped transaction.

See architecture.md, request lifecycle steps 1 to 3.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditContext, set_context
from app.core.config import Settings, get_settings
from app.core.db import apply_context
from app.core.errors import UnauthorizedError
from app.core.security import Principal, decode_access_token

_bearer = HTTPBearer(auto_error=False)

SettingsDep = Annotated[Settings, Depends(get_settings)]
IDEMPOTENT_SESSION = "idempotent_session"


def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    return sessionmaker


SessionmakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]


def get_principal(
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise UnauthorizedError("Missing bearer token.")
    principal = decode_access_token(credentials.credentials, settings)
    if principal is None:
        raise UnauthorizedError("Invalid or expired access token.")
    return principal


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def audit_context(principal: Principal, request_id: str | None) -> AuditContext:
    return AuditContext(
        actor_user_id=principal.user_id,
        client=principal.client,
        request_id=request_id,
        node_id=principal.node_id,
    )


async def get_tenant_session(
    request: Request, sessionmaker: SessionmakerDep, principal: PrincipalDep
) -> AsyncIterator[AsyncSession]:
    """One transaction per request, scoped to the caller's tenant; commits on success, rolls back on error.

    With an ``Idempotency-Key`` the middleware already opened the transaction (so the stored response commits
    together with the business change); the endpoint then works inside it.
    """
    shared: AsyncSession | None = getattr(request.state, IDEMPOTENT_SESSION, None)
    if shared is not None:
        yield shared
        return
    async with sessionmaker() as session, session.begin():
        await apply_context(
            session, tenant_id=principal.tenant_id, user_id=principal.user_id, node_id=principal.node_id
        )
        set_context(session, audit_context(principal, getattr(request.state, "request_id", None)))
        yield session


# scope="function": commit before the response is sent. FastAPI's default ends a dependency with yield after
# the response, so a client could read "201 Created" (and ask for the new document) before the commit, or be
# told a post succeeded that then failed the deferred double-entry check at commit.
TenantSessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]
