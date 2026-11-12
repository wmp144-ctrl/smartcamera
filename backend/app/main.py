"""CameraSmart API application."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import alerts as alerts_api
from app.api import cameras as cameras_api
from app.api import events as events_api
from app.api import objects as objects_api
from app.api import search as search_api
from app.api import stats as stats_api
from app.api import system as system_api
from app.api import ws as ws_api
from app.core.config import settings
from app.core.database import dispose_engine, init_models, session_scope
from app.core.logging import setup_logging
from app.core.timeutil import jalali_label, to_local, utcnow
from app.services.camera_manager import manager
from app.services.retention import apply_retention

logger = logging.getLogger(__name__)

#: First automatic retention pass runs after this delay (start-up stays fast).
RETENTION_STARTUP_DELAY_SECONDS = 300.0


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    settings.ensure_dirs
    # ``CAMERASMART_DEBUG=true`` turns on debug logging for the whole app.
    setup_logging(settings.data_dir / "logs", level=_log_level())
    logger.info("starting %s %s", settings.app_name, settings.version)

    if settings.auto_create_tables:
        try:
            await init_models()
        except Exception:  # noqa: BLE001 - surface but do not crash at boot
            logger.exception("could not create the database schema")

    if settings.autostart_cameras:
        try:
            await manager.start()
        except Exception:  # noqa: BLE001
            logger.exception("camera manager failed to start")

    retention_task: asyncio.Task | None = None
    if settings.retention_auto_run:
        retention_task = asyncio.create_task(_retention_loop(), name="retention")

    try:
        yield
    finally:
        logger.info("shutting down")
        if retention_task is not None:
            retention_task.cancel()
            try:
                await retention_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        try:
            await manager.stop()
        except Exception:  # noqa: BLE001
            logger.exception("camera manager failed to stop cleanly")
        await dispose_engine()


async def _retention_loop() -> None:
    """Trim old data on a timer so an unattended install never fills the disk."""
    await asyncio.sleep(RETENTION_STARTUP_DELAY_SECONDS)
    interval = max(3600.0, float(settings.retention_interval_hours) * 3600.0)
    while True:
        try:
            async with session_scope() as session:
                report = await apply_retention(session)
            logger.info("automatic retention: %s", report.as_dict())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a failed pass must not kill the task
            logger.exception("automatic retention failed")
        await asyncio.sleep(interval)


def _log_level() -> int:
    return logging.DEBUG if settings.debug else logging.INFO


def _mount_frontend(app: FastAPI) -> None:
    """Serve ``frontend/dist`` when it exists, with a SPA fallback."""
    dist = settings.frontend_dist
    if dist is None:
        logger.info("no frontend build found — running in API-only mode")
        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index = dist / "index.html"
    root = dist.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        # Never let the SPA fallback answer an unknown API/media path: those must
        # stay honest 404s so clients can distinguish "route missing" from "app".
        head = full_path.split("/", 1)[0]
        if head in {"api", "media"}:
            raise HTTPException(status_code=404, detail="مسیر یافت نشد")
        if full_path:
            candidate = (root / full_path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index)


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{settings.app_name} API",
        version=settings.version,
        description=(
            "سامانه تشخیص اشیا و حرکت در تصاویر دوربین‌های مداربسته، "
            "ثبت رویداد و اعلام هشدار"
        ),
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in (
        cameras_api.router,
        objects_api.router,
        events_api.router,
        alerts_api.router,
        search_api.router,
        stats_api.router,
        system_api.router,
        ws_api.router,
    ):
        app.include_router(router)

    # Stored images (full / cropped / thumbnails) are served straight from disk.
    settings.ensure_dirs
    app.mount("/media", StaticFiles(directory=settings.storage_root), name="media")

    async def root() -> dict:
        now = utcnow()
        local = to_local(now, settings.timezone) or now
        return {
            "app": settings.app_name,
            "version": settings.version,
            "timezone": settings.timezone,
            "utc": now.isoformat(),
            "jalali": jalali_label(local),
            "docs": "/api/docs",
            "ui": "/dashboard",
            "cameras": manager.stream_count(),
        }

    # The service descriptor always lives on an API path, never on ``/``.
    app.get("/api/info", tags=["system"], response_model=dict)(root)

    # When a build exists, ``/`` must land on the UI: typing the host into a
    # browser is the normal way operators open the app. In API-only mode
    # (no ``frontend/dist``) we keep answering ``/`` with the descriptor.
    if settings.frontend_dist is None:
        app.get("/", include_in_schema=False)(root)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", []))
            details.append({"field": location, "message": error.get("msg", "")})
        return JSONResponse(
            status_code=422,
            content={"detail": "داده‌های ورودی نامعتبر است", "errors": details},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    # Registered last on purpose: the SPA fallback matches every path, so it
    # must never shadow an API route or the JSON root endpoint.
    _mount_frontend(app)

    return app


app = create_app()
