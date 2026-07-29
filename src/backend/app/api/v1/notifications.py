import asyncio
import json
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from app.api.deps import CurrentUser, SessionDep
from app.core.errors import NotFoundError
from app.core.security import now
from app.db.models.collab import Notification
from app.db.models.enums import NotificationType
from app.schemas.requests import NotificationPatch
from app.schemas.serializers import notification as serialize
from app.services.events import bus, user_channel

router = APIRouter(tags=["notifications"])

PING_SECONDS = 20

WEEK_DAYS = 7

WEEK_BUCKETS: dict[str, tuple[NotificationType, ...]] = {
    "research": (NotificationType.RESEARCH_DONE, NotificationType.HANDOVER),
    "proposal": (
        NotificationType.PROPOSAL_DONE,
        NotificationType.APPROVAL_REQUEST,
        NotificationType.APPROVAL_RESULT,
    ),
    "share": (
        NotificationType.SHARE,
        NotificationType.ACCESS_REQUEST,
        NotificationType.INVITE,
    ),
    "comment": (NotificationType.COMMENT,),
}


@router.get("/notifications", summary="Danh sách thông báo")
async def list_notifications(
    user: CurrentUser,
    session: SessionDep,
    unreadOnly: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    stmt = (
        select(Notification)
        .where(Notification.user_id == user.id)
        .options(selectinload(Notification.actor))
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    if unreadOnly:
        stmt = stmt.where(Notification.read.is_(False))

    rows = (await session.execute(stmt)).unique().scalars().all()
    unread = await _unread_count(session, user.id)
    return {
        "items": [serialize(n) for n in rows],
        "total": len(rows),
        "unreadCount": unread,
        "weekSummary": await _week_summary(session, user.id),
    }


async def _week_summary(session: SessionDep, user_id: str) -> dict[str, int]:
    rows = (
        await session.execute(
            select(Notification.type, func.count())
            .where(
                Notification.user_id == user_id,
                Notification.created_at >= now() - timedelta(days=WEEK_DAYS),
            )
            .group_by(Notification.type)
        )
    ).all()

    per_type = {str(kind): int(count or 0) for kind, count in rows}
    summary = {
        bucket: sum(per_type.get(str(kind), 0) for kind in kinds)
        for bucket, kinds in WEEK_BUCKETS.items()
    }
    summary["total"] = sum(per_type.values())
    return summary


async def _unread_count(session: SessionDep, user_id: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == user_id, Notification.read.is_(False))
            )
        ).scalar_one()
        or 0
    )


@router.get("/notifications/unread-count", summary="Số thông báo chưa đọc")
async def unread_count(user: CurrentUser, session: SessionDep) -> dict[str, int]:
    return {"unreadCount": await _unread_count(session, user.id)}


@router.post("/notifications/{notification_id}/read", summary="Đánh dấu đã đọc")
async def mark_read(notification_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    row = await session.get(Notification, notification_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("Thông báo")
    if not row.read:
        row.read = True
        row.read_at = now()
        await session.commit()
    return {**serialize(row), "unreadCount": await _unread_count(session, user.id)}


@router.patch("/notifications/{notification_id}", summary="Đổi trạng thái đã đọc")
async def patch_notification(
    notification_id: str,
    body: NotificationPatch,
    user: CurrentUser,
    session: SessionDep,
) -> dict[str, Any]:
    row = await session.get(Notification, notification_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("Thông báo")
    if row.read != body.read:
        row.read = body.read
        row.read_at = now() if body.read else None
        await session.commit()
    return {**serialize(row), "unreadCount": await _unread_count(session, user.id)}


@router.post("/notifications/read-all", summary="Đánh dấu tất cả đã đọc")
async def mark_all_read(user: CurrentUser, session: SessionDep) -> dict[str, int]:
    result = await session.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read.is_(False))
        .values(read=True, read_at=now())
    )
    await session.commit()
    return {"updated": int(result.rowcount or 0), "unreadCount": 0}


@router.delete(
    "/notifications/{notification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Xoá thông báo",
)
async def delete_notification(notification_id: str, user: CurrentUser, session: SessionDep) -> None:
    row = await session.get(Notification, notification_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("Thông báo")
    await session.delete(row)
    await session.commit()


@router.get("/notifications/stream", summary="SSE thông báo realtime")
async def stream_notifications(
    user: CurrentUser, session: SessionDep, request: Request
) -> EventSourceResponse:

    async def event_stream() -> AsyncIterator[dict[str, str]]:

        yield {
            "event": "unread",
            "data": json.dumps({"unreadCount": await _unread_count(session, user.id)}),
        }

        channel = user_channel(user.id)
        client = await bus.redis()
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(channel)
        try:
            while True:
                if await request.is_disconnected():
                    break
                message = await pubsub.get_message(timeout=1.0)
                if message is None:
                    await asyncio.sleep(0)
                    continue
                try:
                    parsed = json.loads(message.get("data"))
                except (TypeError, json.JSONDecodeError):
                    continue
                yield {
                    "event": parsed.get("event", "notification"),
                    "data": json.dumps(parsed.get("data") or {}, ensure_ascii=False, default=str),
                }
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return EventSourceResponse(
        event_stream(),
        ping=PING_SECONDS,
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
