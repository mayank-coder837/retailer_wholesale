"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.core import health
from app.core.config import JobsBackend, Settings, get_settings
from app.core.db import create_engine, create_sessionmaker
from app.core.errors import register_error_handlers
from app.core.idempotency import IdempotencyMiddleware
from app.core.jobs import Worker
from app.core.request_id import RequestIdMiddleware
from app.core.storage import LocalStorage
from app.modules.identity import roles_router
from app.modules.identity import router as identity_router
from app.modules.platform import attachments
from app.modules.platform import router as platform_router
from app.modules.tenancy import router as tenancy_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        app.state.storage = LocalStorage(settings.files_dir)
        attachments.configure(app.state.storage)
        worker = Worker(app.state.sessionmaker)
        app.state.worker = worker
        if settings.worker_enabled and settings.jobs_backend is JobsBackend.INPROCESS:
            worker.start()
        yield
        await worker.stop()
        await engine.dispose()

    app = FastAPI(
        title=f"{settings.product.name} API",
        version=__version__,
        lifespan=lifespan,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    # Last added runs first: request id → CORS → idempotency → routes.
    app.add_middleware(IdempotencyMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # The web app sends the HttpOnly refresh cookie to /auth (M0.5). Only explicitly listed origins.
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Idempotent-Replayed"],
    )
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(identity_router.router)
    app.include_router(tenancy_router.router)
    app.include_router(roles_router.router)
    app.include_router(platform_router.router)
    return app


app = create_app()
