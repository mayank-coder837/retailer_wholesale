"""Typed domain errors rendered as RFC 7807 problem responses with a stable ``code``."""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"


class DomainError(Exception):
    """Base for every error the API reports on purpose. Subclasses set ``code``, ``status`` and ``title``."""

    code = "domain_error"
    status = HTTPStatus.BAD_REQUEST
    title = "Request could not be processed"

    def __init__(self, detail: str | None = None, **extra: Any) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.extra = extra


class DatabaseUnavailableError(DomainError):
    code = "database_unavailable"
    status = HTTPStatus.SERVICE_UNAVAILABLE
    title = "Database unavailable"


class UnauthorizedError(DomainError):
    code = "unauthorized"
    status = HTTPStatus.UNAUTHORIZED
    title = "Authentication required"


class InvalidCredentialsError(UnauthorizedError):
    code = "invalid_credentials"
    title = "Email or password is incorrect"


class AccountLockedError(DomainError):
    code = "account_locked"
    status = HTTPStatus.LOCKED
    title = "Too many failed attempts; try again later"


class InvalidRefreshTokenError(UnauthorizedError):
    code = "invalid_refresh_token"
    title = "Session expired; log in again"


class RefreshTokenReusedError(UnauthorizedError):
    code = "refresh_token_reused"
    title = "This session was ended for safety; log in again"


class NotFoundError(DomainError):
    code = "not_found"
    status = HTTPStatus.NOT_FOUND
    title = "Not found"


class ConflictError(DomainError):
    code = "conflict"
    status = HTTPStatus.CONFLICT
    title = "Conflict"


class EmailTakenError(ConflictError):
    code = "email_taken"
    title = "An account with this email already exists"


class TenantSelectionRequiredError(ConflictError):
    code = "tenant_selection_required"
    title = "Choose which account to open"


def problem(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    **extra: Any,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://errors.smartbiz.local/{code}",
        "title": title,
        "status": status,
        "code": code,
        "instance": request.url.path,
        **extra,
    }
    if detail:
        body["detail"] = detail
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


async def _domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    return problem(request, status=exc.status, code=exc.code, title=exc.title, detail=exc.detail, **exc.extra)


async def _http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    status = HTTPStatus(exc.status_code)
    return problem(
        request,
        status=status,
        code=status.phrase.lower().replace(" ", "_"),
        title=status.phrase,
        detail=str(exc.detail) if exc.detail != status.phrase else None,
    )


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return problem(
        request,
        status=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="validation_error",
        title="Invalid request",
        errors=[{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()],
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
