from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, ORJSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import (
    AppError,
    app_error_handler,
    http_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.logging import get_logger, setup_logging
from app.db.session import dispose_engine
from app.integrations.maas import maas
from app.integrations.resend_client import resend
from app.integrations.search import search_client
from app.services.events import bus
from app.services.storage import storage

log = get_logger(__name__)


FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

FAVICON_TYPES = {
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}

FAVICON_CANDIDATES = [
    "assets/favicon.ico",
    "assets/favicon.svg",
    "assets/favicon.png",
    "favicon.ico",
    "favicon.svg",
    "favicon.png",
]


def favicon_file() -> Path | None:
    for name in FAVICON_CANDIDATES:
        candidate = FRONTEND_DIR / name
        if candidate.is_file():
            return candidate
    return None


PROTOTYPE_CANDIDATES = (
    "ZB Webapp Prototype (Standalone).html",
    "ZB Webapp Prototype.dc.html",
)


def prototype_file() -> Path | None:
    for name in PROTOTYPE_CANDIDATES:
        candidate = FRONTEND_DIR / name
        if candidate.is_file():
            return candidate
    return None


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    log.info(
        "starting",
        env=settings.app_env,
        maas=settings.maas_configured,
        search=settings.search_configured,
        resend=settings.resend_configured,
        email_dry_run=settings.email_dry_run,
    )

    try:
        created = await storage.ensure_buckets()
        if created:
            log.info("buckets_created", buckets=created)
    except Exception as exc:
        log.warning("bucket_init_failed", error=str(exc))

    if not await bus.health():
        log.warning("redis_unreachable", url=settings.redis_url)

    if settings.bootstrap_admin_emails:
        try:
            from app.db.session import session_scope
            from app.users import ensure_bootstrap_admins

            async with session_scope() as session:
                touched = await ensure_bootstrap_admins(session)
            if touched:
                log.info("bootstrap_admins", changed=touched)
        except Exception as exc:
            log.warning("bootstrap_admins_failed", error=str(exc))

    yield

    await bus.aclose()
    await maas.aclose()
    await resend.aclose()
    await search_client.aclose()
    await dispose_engine()
    log.info("stopped")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Backend for the ZB Webapp prototype. Implements the contract in "
        "API-Backend.md — auth/OTP, customers + Deep Research, proposals + "
        "editor, knowledge, skills, templates, sharing, admin."
    ),
    default_response_class=ORJSONResponse,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Last-Event-ID", "Content-Disposition"],
)

app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(HTTPException, http_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(Exception, unhandled_error_handler)

app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/api", include_in_schema=False)
async def api_root() -> dict[str, Any]:
    return {
        "name": settings.app_name,
        "version": "0.1.0",
        "apiBase": settings.api_prefix,
        "docs": "/docs",
    }


if FRONTEND_DIR.is_dir():

    @app.get("/", include_in_schema=False)
    async def prototype() -> Any:
        found = prototype_file()
        if found is not None:
            from app.services.prototype import cache as prototype_cache

            return HTMLResponse(
                prototype_cache.render(found),
                headers={"Cache-Control": "no-store, must-revalidate"},
            )
        return ORJSONResponse(
            {
                "error": {
                    "code": "FRONTEND_MISSING",
                    "message": (
                        "Không tìm thấy prototype trong src/frontend. Cần một trong: "
                        + ", ".join(PROTOTYPE_CANDIDATES)
                    ),
                    "field": None,
                }
            },
            status_code=404,
        )

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Any:
        found = favicon_file()
        if found is None:
            return Response(status_code=204)
        return FileResponse(found, media_type=FAVICON_TYPES[found.suffix.lower()])

    @app.get("/zb-api-bridge.js", include_in_schema=False)
    async def bridge_js() -> Any:
        # Phải đi trước mount StaticFiles. StaticFiles chỉ gắn ETag, không gắn
        # Cache-Control, nên trình duyệt được phép dùng lại bản cũ mà không hỏi
        # lại — bridge cũ + HTML mới là nguồn gốc lỗi 'xoá rồi vẫn còn'.
        found = FRONTEND_DIR / "zb-api-bridge.js"
        if not found.is_file():
            return Response(status_code=404)
        return FileResponse(
            found,
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


@app.middleware("http")
async def request_context(request: Request, call_next: Any) -> Any:
    import structlog

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        method=request.method,
        path=request.url.path,
    )
    return await call_next(request)
