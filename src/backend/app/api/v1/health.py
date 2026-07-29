from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import SessionDep
from app.core.config import settings
from app.integrations.search import search_client
from app.services.events import bus
from app.services.storage import storage

router = APIRouter(tags=["health"])


@router.get("/health", summary="Trạng thái dịch vụ")
async def health(session: SessionDep, response: Response) -> dict[str, Any]:
    degraded: list[str] = []

    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
        degraded.append("db")

    if not await bus.health():
        degraded.append("redis")
    if not await storage.health():
        degraded.append("storage")

    if not settings.maas_configured:
        degraded.append("ai")
    if not search_client.configured:
        degraded.append("search")

    hard_down = [d for d in degraded if d in ("db", "redis", "storage")]
    if hard_down:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "down" if hard_down else ("degraded" if degraded else "ok"),
        "degraded": degraded,
        "checks": {
            "db": db_ok,
            "redis": "redis" not in degraded,
            "storage": "storage" not in degraded,
            "ai": settings.maas_configured,
            "search": search_client.configured,
            "email": settings.resend_configured or settings.email_dry_run,
        },
        "version": "0.1.0",
        "env": settings.app_env,
    }


@router.get("/health/live", summary="Liveness", include_in_schema=False)
async def live() -> dict[str, str]:
    return {"status": "ok"}
