from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.text import normalize
from app.db.models.collab import AuditLog
from app.db.models.enums import AuditCategory
from app.db.models.user import User

_CATEGORY_BY_PREFIX: dict[str, AuditCategory] = {
    "customer": AuditCategory.CUSTOMER_DATA,
    "fact": AuditCategory.CUSTOMER_DATA,
    "handover": AuditCategory.CUSTOMER_DATA,
    "research": AuditCategory.CUSTOMER_DATA,
    "solution": AuditCategory.CUSTOMER_DATA,
    "proposal": AuditCategory.PROPOSAL,
    "slide": AuditCategory.PROPOSAL,
    "comment": AuditCategory.PROPOSAL,
    "export": AuditCategory.PROPOSAL,
    "approval": AuditCategory.PROPOSAL,
    "share": AuditCategory.PERMISSIONS,
    "access_request": AuditCategory.PERMISSIONS,
    "user": AuditCategory.PERMISSIONS,
    "role": AuditCategory.PERMISSIONS,
    "invite": AuditCategory.PERMISSIONS,
    "auth": AuditCategory.PERMISSIONS,
    "kb": AuditCategory.KNOWLEDGE,
    "knowledge": AuditCategory.KNOWLEDGE,
    "skill": AuditCategory.KNOWLEDGE,
    "template": AuditCategory.KNOWLEDGE,
    "product": AuditCategory.KNOWLEDGE,
}


def category_for(action: str) -> AuditCategory:
    return _CATEGORY_BY_PREFIX.get(action.split(".", 1)[0], AuditCategory.SYSTEM)


async def record(
    session: AsyncSession,
    *,
    action: str,
    summary: str,
    actor: User | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    resource_label: str | None = None,
    category: AuditCategory | None = None,
    meta: dict[str, Any] | None = None,
    request: Request | None = None,
) -> AuditLog:
    entry = AuditLog(
        id=AuditLog.make_id(),
        actor_id=actor.id if actor else None,
        actor_label=actor.name if actor else None,
        category=category or category_for(action),
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_label=resource_label,
        summary=summary,
        search_norm=normalize(
            " ".join(filter(None, [summary, resource_label, actor.name if actor else None]))
        ),
        meta=meta or {},
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent") if request else None,
    )
    session.add(entry)
    return entry


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
