from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ForbiddenError, NotFoundError
from app.db.base import Base
from app.db.models.admin import SystemPolicy
from app.db.models.collab import Notification, Share, ShareLink
from app.db.models.customer import Customer
from app.db.models.enums import (
    LinkMode,
    Permission,
    RequestStatus,
    ResourceType,
    Scope,
)
from app.db.models.knowledge import KnowledgeBase
from app.db.models.library import ProposalTemplate, Skill
from app.db.models.proposal import Proposal
from app.db.models.user import AccessRequest, User

_RESOURCES: dict[ResourceType, tuple[type[Base], str, str]] = {
    ResourceType.CUSTOMER: (Customer, "owner_id", "name"),
    ResourceType.PROPOSAL: (Proposal, "owner_id", "title"),
    ResourceType.KNOWLEDGE_BASE: (KnowledgeBase, "owner_id", "name"),
    ResourceType.SKILL: (Skill, "owner_id", "name"),
    ResourceType.TEMPLATE: (ProposalTemplate, "owner_id", "name"),
}

_RANK = {Permission.NONE: 0, Permission.VIEW: 1, Permission.COMMENT: 2, Permission.EDIT: 3}


KIND_LABEL: dict[ResourceType, str] = {
    ResourceType.CUSTOMER: "khách hàng",
    ResourceType.PROPOSAL: "proposal",
    ResourceType.KNOWLEDGE_BASE: "knowledge base",
    ResourceType.SKILL: "skill",
    ResourceType.TEMPLATE: "proposal template",
}


_URL_PREFIX: dict[ResourceType, str] = {
    ResourceType.CUSTOMER: "/customers",
    ResourceType.PROPOSAL: "/proposals",
    ResourceType.KNOWLEDGE_BASE: "/knowledge",
    ResourceType.SKILL: "/skills",
    ResourceType.TEMPLATE: "/templates",
}


def model_for(resource_type: ResourceType) -> type[Base]:
    return _RESOURCES[resource_type][0]


def owner_field(resource_type: ResourceType) -> str:
    return _RESOURCES[resource_type][1]


def label_of(resource_type: ResourceType, obj: Any) -> str:
    return str(getattr(obj, _RESOURCES[resource_type][2], "") or obj.id)


def target_url(resource_type: ResourceType, resource_id: str) -> str:
    return f"{_URL_PREFIX[resource_type]}/{resource_id}"


def at_least(granted: Permission | None, required: Permission) -> bool:
    return _RANK.get(granted or Permission.NONE, 0) >= _RANK[required]


async def purge_links(
    session: AsyncSession, resource_type: ResourceType, resource_id: str
) -> dict[str, int]:
    rt = str(resource_type)
    removed: dict[str, int] = {}

    for label, model in (("shares", Share), ("shareLinks", ShareLink)):
        rows = (
            (
                await session.execute(
                    select(model).where(
                        model.resource_type == rt,
                        model.resource_id == resource_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            await session.delete(row)
        removed[label] = len(rows)

    url = target_url(resource_type, resource_id)
    notes = (
        (await session.execute(select(Notification).where(Notification.target_url == url)))
        .scalars()
        .all()
    )
    for note in notes:
        await session.delete(note)
    removed["notifications"] = len(notes)

    requests = (
        (
            await session.execute(
                select(AccessRequest).where(
                    AccessRequest.resource_type == rt,
                    AccessRequest.resource_id == resource_id,
                )
            )
        )
        .scalars()
        .all()
    )
    for entry in requests:
        await session.delete(entry)
    removed["accessRequests"] = len(requests)

    return removed


@dataclass(slots=True)
class Access:
    permission: Permission
    reason: str

    @property
    def allowed(self) -> bool:
        return self.permission != Permission.NONE

    @property
    def can_edit(self) -> bool:
        return at_least(self.permission, Permission.EDIT)

    @property
    def can_comment(self) -> bool:
        return at_least(self.permission, Permission.COMMENT)


def admin_override(user: User) -> bool:
    """Quyền admin có vượt qua isolation không. Tách riêng để ba nhánh (resolve,
    visible_filter, apply_visibility) không lệch nhau."""
    return user.is_admin and settings.admin_sees_all_resources


async def load(session: AsyncSession, resource_type: ResourceType, resource_id: str) -> Any:
    model = model_for(resource_type)
    obj = await session.get(model, resource_id)
    if obj is None:
        raise NotFoundError(KIND_LABEL[resource_type].capitalize())
    return obj


async def resolve(
    session: AsyncSession,
    user: User,
    resource_type: ResourceType,
    resource_id: str,
    obj: Any | None = None,
) -> Access:
    if obj is None:
        obj = await load(session, resource_type, resource_id)

    if getattr(obj, owner_field(resource_type), None) == user.id:
        return Access(Permission.EDIT, "owner")
    if admin_override(user):
        return Access(Permission.EDIT, "admin")

    direct = (
        await session.execute(
            select(Share).where(
                Share.resource_type == resource_type,
                Share.resource_id == resource_id,
                Share.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if direct:
        return Access(direct.permission, "user_share")

    if user.team_id:
        team_share = (
            await session.execute(
                select(Share).where(
                    Share.resource_type == resource_type,
                    Share.resource_id == resource_id,
                    Share.team_id == user.team_id,
                )
            )
        ).scalar_one_or_none()
        if team_share:
            return Access(team_share.permission, "team_share")

    link = (
        await session.execute(
            select(ShareLink).where(
                ShareLink.resource_type == resource_type,
                ShareLink.resource_id == resource_id,
            )
        )
    ).scalar_one_or_none()
    if link and link.mode == LinkMode.COMPANY:
        policy = await session.get(SystemPolicy, 1)

        if policy is None or policy.allow_company_link:
            return Access(link.permission, "company_link")

    if getattr(obj, "scope", None) == Scope.SHARED and not getattr(obj, "restricted", False):
        return Access(Permission.VIEW, "shared_scope")

    return Access(Permission.NONE, "none")


async def require(
    session: AsyncSession,
    user: User,
    resource_type: ResourceType,
    resource_id: str,
    permission: Permission = Permission.VIEW,
    obj: Any | None = None,
) -> tuple[Any, Access]:
    if obj is None:
        obj = await load(session, resource_type, resource_id)
    access = await resolve(session, user, resource_type, resource_id, obj)
    if not at_least(access.permission, permission):
        raise ForbiddenError(
            f"Bạn không có quyền {permission.value} với {KIND_LABEL[resource_type]} này."
        )
    return obj, access


def visible_filter(resource_type: ResourceType, user: User) -> Any:
    model = model_for(resource_type)
    owner_col = getattr(model, owner_field(resource_type))

    if admin_override(user):
        return owner_col.is_not(None) | owner_col.is_(None)

    shared_ids = select(Share.resource_id).where(
        Share.resource_type == resource_type,
        or_(
            Share.user_id == user.id,
            Share.team_id == user.team_id if user.team_id else Share.team_id.is_(None) & False,
        ),
    )
    linked_ids = select(ShareLink.resource_id).where(
        ShareLink.resource_type == resource_type,
        ShareLink.mode == LinkMode.COMPANY,
    )

    clauses = [owner_col == user.id, model.id.in_(shared_ids), model.id.in_(linked_ids)]
    if hasattr(model, "is_builtin"):
        clauses.append(model.is_builtin.is_(True))
    if hasattr(model, "scope"):
        published = model.scope == Scope.SHARED
        if hasattr(model, "restricted"):
            published = published & (model.restricted.is_(False))
        clauses.append(published)
    return or_(*clauses)


def apply_visibility(stmt: Select[Any], resource_type: ResourceType, user: User) -> Select[Any]:
    if admin_override(user):
        return stmt
    return stmt.where(visible_filter(resource_type, user))


async def dialog_state(
    session: AsyncSession,
    user: User,
    resource_type: ResourceType,
    resource_id: str,
) -> dict[str, Any]:
    obj, access = await require(session, user, resource_type, resource_id, Permission.VIEW)

    owner_id = getattr(obj, owner_field(resource_type), None)
    owner = await session.get(User, owner_id) if owner_id else None

    shares = (
        (
            await session.execute(
                select(Share)
                .where(Share.resource_type == resource_type, Share.resource_id == resource_id)
                .order_by(Share.created_at)
            )
        )
        .scalars()
        .all()
    )
    link = (
        await session.execute(
            select(ShareLink).where(
                ShareLink.resource_type == resource_type,
                ShareLink.resource_id == resource_id,
            )
        )
    ).scalar_one_or_none()

    pending: list[AccessRequest] = []
    if access.can_edit:
        pending = list(
            (
                await session.execute(
                    select(AccessRequest)
                    .where(
                        AccessRequest.resource_type == resource_type,
                        AccessRequest.resource_id == resource_id,
                        AccessRequest.status == RequestStatus.PENDING,
                    )
                    .order_by(AccessRequest.created_at)
                )
            )
            .scalars()
            .all()
        )

    return {
        "owner": _person(owner),
        "scope": str(getattr(obj, "scope", Scope.MINE)),
        "people": [
            {
                "id": s.id,
                "user": _person(s.user) if s.user else None,
                "team": {"id": s.team.id, "name": s.team.name, "type": "team"} if s.team else None,
                "permission": str(s.permission),
            }
            for s in shares
        ],
        "link": {
            "mode": str(link.mode) if link else str(LinkMode.RESTRICTED),
            "permission": str(link.permission) if link else str(Permission.VIEW),
        },
        "pendingRequests": [
            {
                "id": r.id,
                "user": _person(r.requester)
                if r.requester
                else {"email": r.requester_email, "name": r.requester_name or r.requester_email},
                "permission": str(r.permission or Permission.VIEW),
                "reason": r.reason,
                "createdAt": r.created_at,
            }
            for r in pending
        ],
        "myPermission": str(access.permission),
        "canManage": access.can_edit,
    }


def _person(user: User | None) -> dict[str, Any] | None:
    if user is None:
        return None
    return {
        "id": user.id,
        "handle": user.handle,
        "email": user.email,
        "name": user.name,
        "initials": user.initials,
        "team": user.display_team,
        "avatarColor": user.avatar_color,
    }
