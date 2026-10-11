"""Auth endpoints.

Each runs its own transaction, so failed-login counters and token revocations are committed even when the
request ends in an error.
"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Response, status

from app.core.config import Settings
from app.core.deps import PrincipalDep, SessionmakerDep, SettingsDep, TenantSessionDep
from app.core.errors import DomainError, InvalidRefreshTokenError
from app.modules.identity import service
from app.modules.identity.models import ClientKind
from app.modules.identity.schemas import LoginIn, MeOut, RefreshIn, SignupIn, SwitchTenantIn, TokenOut

router = APIRouter(tags=["auth"])

REFRESH_COOKIE = "smartbiz_refresh"
REFRESH_COOKIE_PATH = "/auth"
RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE)]


def _respond(tokens: service.IssuedTokens, response: Response, settings: Settings) -> TokenOut:
    """Desktop gets the refresh token in the body (kept in safeStorage); web gets an HttpOnly cookie."""
    body_token = None
    if tokens.client is ClientKind.DESKTOP:
        body_token = tokens.refresh_token
    else:
        response.set_cookie(
            REFRESH_COOKIE,
            tokens.refresh_token,
            max_age=settings.refresh_token_ttl_days * 86400,
            path=REFRESH_COOKIE_PATH,
            httponly=True,
            secure=settings.refresh_cookie_secure,
            samesite="strict",
        )
    return TokenOut(
        access_token=tokens.access_token,
        expires_in=tokens.expires_in,
        refresh_token=body_token,
        tenant_id=tokens.tenant_id,
        membership_id=tokens.membership_id,
    )


def _unwrap(result: service.IssuedTokens | DomainError) -> service.IssuedTokens:
    if isinstance(result, DomainError):
        raise result
    return result


@router.post("/auth/signup", operation_id="signup", status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupIn, response: Response, sessionmaker: SessionmakerDep, settings: SettingsDep
) -> TokenOut:
    async with sessionmaker() as session, session.begin():
        tokens = await service.signup(session, settings, body)
    return _respond(tokens, response, settings)


@router.post(
    "/auth/login",
    operation_id="login",
    responses={
        401: {"description": "`invalid_credentials`"},
        409: {"description": "`tenant_selection_required` — lists the user's accounts"},
        423: {"description": "`account_locked`"},
    },
)
async def login(
    body: LoginIn, response: Response, sessionmaker: SessionmakerDep, settings: SettingsDep
) -> TokenOut:
    async with sessionmaker() as session, session.begin():
        result = await service.login(session, settings, body)
    return _respond(_unwrap(result), response, settings)


@router.post("/auth/refresh", operation_id="refresh", responses={401: {"description": "Session ended"}})
async def refresh(
    body: RefreshIn,
    response: Response,
    sessionmaker: SessionmakerDep,
    settings: SettingsDep,
    cookie_token: RefreshCookie = None,
) -> TokenOut:
    raw = body.refresh_token or cookie_token
    if not raw:
        raise InvalidRefreshTokenError()
    async with sessionmaker() as session, session.begin():
        result = await service.refresh(session, settings, raw)
    return _respond(_unwrap(result), response, settings)


@router.post("/auth/logout", operation_id="logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: RefreshIn, response: Response, sessionmaker: SessionmakerDep, cookie_token: RefreshCookie = None
) -> None:
    raw = body.refresh_token or cookie_token
    if raw:
        async with sessionmaker() as session, session.begin():
            await service.logout(session, raw)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)


@router.post("/auth/switch-tenant", operation_id="switch_tenant")
async def switch_tenant(
    body: SwitchTenantIn,
    response: Response,
    principal: PrincipalDep,
    sessionmaker: SessionmakerDep,
    settings: SettingsDep,
) -> TokenOut:
    async with sessionmaker() as session, session.begin():
        result = await service.switch_tenant(session, settings, principal, body.tenant_id, body.client)
    return _respond(_unwrap(result), response, settings)


@router.get("/me", operation_id="me")
async def me(principal: PrincipalDep, session: TenantSessionDep) -> MeOut:
    return await service.me(session, principal)
