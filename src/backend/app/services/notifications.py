from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models.collab import Notification
from app.db.models.enums import NotificationType
from app.db.models.user import User, UserNotificationSettings
from app.integrations.resend_client import resend
from app.services.events import bus

log = get_logger(__name__)


_EMAIL_TOGGLE: dict[NotificationType, str] = {
    NotificationType.RESEARCH_DONE: "email_on_research_done",
    NotificationType.APPROVAL_REQUEST: "email_on_approval_request",
    NotificationType.APPROVAL_RESULT: "email_on_approval_request",
    NotificationType.COMMENT: "email_on_comment",
    NotificationType.SHARE: "email_on_share",
}


async def wants_email(session: AsyncSession, user_id: str, kind: NotificationType) -> bool:
    field = _EMAIL_TOGGLE.get(kind)
    if field is None:
        return kind in (
            NotificationType.INVITE,
            NotificationType.HANDOVER,
            NotificationType.ACCESS_REQUEST,
        )
    prefs = (
        await session.execute(
            select(UserNotificationSettings).where(UserNotificationSettings.user_id == user_id)
        )
    ).scalar_one_or_none()
    if prefs is None:
        return field != "email_on_comment"
    return bool(getattr(prefs, field, False))


async def push(
    session: AsyncSession,
    *,
    user_id: str,
    kind: NotificationType,
    title: str,
    body: str | None = None,
    target_url: str | None = None,
    actor_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> Notification:
    notification = Notification(
        id=Notification.make_id(),
        user_id=user_id,
        type=kind,
        title=title,
        body=body,
        target_url=target_url,
        actor_id=actor_id,
        meta=meta or {},
    )
    session.add(notification)
    await session.flush()

    await bus.notify_user(
        user_id,
        {
            "id": notification.id,
            "type": str(kind),
            "title": title,
            "body": body,
            "targetUrl": target_url,
            "read": False,
        },
    )
    return notification


async def push_many(
    session: AsyncSession,
    *,
    user_ids: list[str],
    kind: NotificationType,
    title: str,
    body: str | None = None,
    target_url: str | None = None,
    actor_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> list[Notification]:
    out: list[Notification] = []
    for user_id in dict.fromkeys(user_ids):
        if user_id == actor_id:
            continue
        out.append(
            await push(
                session,
                user_id=user_id,
                kind=kind,
                title=title,
                body=body,
                target_url=target_url,
                actor_id=actor_id,
                meta=meta,
            )
        )
    return out


async def email_if_opted_in(
    session: AsyncSession, user: User, kind: NotificationType, send: Any
) -> None:
    if not await wants_email(session, user.id, kind):
        return
    try:
        await send()
    except Exception as exc:
        log.warning("notification_email_failed", user=user.email, kind=str(kind), error=str(exc))


__all__ = ["email_if_opted_in", "push", "push_many", "resend", "wants_email"]
