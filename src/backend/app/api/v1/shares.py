from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, SessionDep
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.security import now, share_link_token
from app.core.text import search_term
from app.db.models.admin import SystemPolicy
from app.db.models.collab import Share, ShareLink
from app.db.models.enums import (
    AccessRequestType,
    LinkMode,
    NotificationType,
    Permission,
    RequestStatus,
    ResourceType,
    Scope,
)
from app.db.models.user import AccessRequest, Team, User
from app.integrations.resend_client import resend
from app.schemas.requests import (
    DecisionBody,
    RejectBody,
    ShareCreate,
    ShareLinkPatch,
    SharePatch,
)
from app.schemas.serializers import (
    access_request as serialize_request,
)
from app.schemas.serializers import (
    directory_entry,
    team_entry,
)
from app.services import audit
from app.services.notifications import email_if_opted_in, push
from app.services.shares import (
    KIND_LABEL,
    dialog_state,
    label_of,
    load,
    owner_field,
    require,
    target_url,
)

router = APIRouter(tags=["shares"])


@router.get("/shares", summary="Trạng thái chia sẻ của một tài nguyên")
async def get_shares(
    user: CurrentUser,
    session: SessionDep,
    resourceType: Annotated[ResourceType, Query()],
    resourceId: Annotated[str, Query()],
) -> dict[str, Any]:
    return await dialog_state(session, user, resourceType, resourceId)


@router.post("/shares", status_code=status.HTTP_201_CREATED, summary="Chia sẻ cho người / nhóm")
async def create_shares(
    body: ShareCreate, user: CurrentUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    obj, _ = await require(session, user, body.resourceType, body.resourceId, Permission.EDIT)
    if not body.userIds and not body.teamIds:
        raise ValidationError("Cần chọn ít nhất một người hoặc nhóm.", field="userIds")

    label = label_of(body.resourceType, obj)
    url = target_url(body.resourceType, body.resourceId)
    kind_label = KIND_LABEL[body.resourceType]
    granted: list[dict[str, Any]] = []

    for user_id in dict.fromkeys(body.userIds):
        recipient = await session.get(User, user_id)
        if recipient is None:
            raise NotFoundError(f"Người dùng {user_id}")
        if getattr(obj, owner_field(body.resourceType), None) == recipient.id:
            continue

        existing = (
            await session.execute(
                select(Share).where(
                    Share.resource_type == body.resourceType,
                    Share.resource_id == body.resourceId,
                    Share.user_id == recipient.id,
                )
            )
        ).scalar_one_or_none()

        if existing:
            existing.permission = body.permission
            row = existing
        else:
            row = Share(
                id=Share.make_id(),
                resource_type=body.resourceType,
                resource_id=body.resourceId,
                user_id=recipient.id,
                permission=body.permission,
                created_by_id=user.id,
            )
            session.add(row)
            await session.flush()

        granted.append({"id": row.id, "userId": recipient.id, "permission": str(row.permission)})

        if body.notify:
            await push(
                session,
                user_id=recipient.id,
                kind=NotificationType.SHARE,
                title=f"{user.name} đã chia sẻ {kind_label}",
                body=f"{label} — quyền {body.permission.value}.",
                target_url=url,
                actor_id=user.id,
            )
            await email_if_opted_in(
                session,
                recipient,
                NotificationType.SHARE,
                lambda r=recipient: resend.send_share(
                    r.email,
                    actor_name=user.name,
                    resource_label=label,
                    resource_kind=kind_label,
                    permission=body.permission.value,
                    url=url,
                ),
            )

    for team_id in dict.fromkeys(body.teamIds):
        team = await session.get(Team, team_id)
        if team is None:
            raise NotFoundError(f"Nhóm {team_id}")
        existing_team = (
            await session.execute(
                select(Share).where(
                    Share.resource_type == body.resourceType,
                    Share.resource_id == body.resourceId,
                    Share.team_id == team.id,
                )
            )
        ).scalar_one_or_none()
        if existing_team:
            existing_team.permission = body.permission
            row = existing_team
        else:
            row = Share(
                id=Share.make_id(),
                resource_type=body.resourceType,
                resource_id=body.resourceId,
                team_id=team.id,
                permission=body.permission,
                created_by_id=user.id,
            )
            session.add(row)
            await session.flush()
        granted.append({"id": row.id, "teamId": team.id, "permission": str(row.permission)})

    await audit.record(
        session,
        action="share.grant",
        summary=(
            f"{user.name} chia sẻ {kind_label} “{label}” cho "
            f"{len(body.userIds)} người, {len(body.teamIds)} nhóm ({body.permission.value})"
        ),
        actor=user,
        resource_type=str(body.resourceType),
        resource_id=body.resourceId,
        resource_label=label,
        meta={"userIds": body.userIds, "teamIds": body.teamIds, "permission": str(body.permission)},
        request=request,
    )
    await session.commit()
    return {
        "granted": granted,
        **await dialog_state(session, user, body.resourceType, body.resourceId),
    }


@router.patch("/shares/link", summary="Bật / tắt liên kết toàn công ty")
async def update_share_link(
    body: ShareLinkPatch, user: CurrentUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    obj, _ = await require(session, user, body.resourceType, body.resourceId, Permission.EDIT)

    if body.mode == LinkMode.COMPANY:
        policy = await session.get(SystemPolicy, 1)

        if policy is not None and not policy.allow_company_link:
            raise ForbiddenError(
                "Chính sách hệ thống đang tắt liên kết toàn công ty. Liên hệ Admin."
            )

    row = (
        await session.execute(
            select(ShareLink).where(
                ShareLink.resource_type == body.resourceType,
                ShareLink.resource_id == body.resourceId,
            )
        )
    ).scalar_one_or_none()

    if row is None:
        row = ShareLink(
            id=ShareLink.make_id(),
            resource_type=body.resourceType,
            resource_id=body.resourceId,
            mode=body.mode,
            permission=body.permission,
            token=share_link_token(),
            updated_by_id=user.id,
        )
        session.add(row)
    else:
        row.mode = body.mode
        row.permission = body.permission
        row.updated_by_id = user.id

    await audit.record(
        session,
        action="share.link",
        summary=(
            f"{user.name} đặt liên kết “{label_of(body.resourceType, obj)}” thành "
            f"{body.mode.value} ({body.permission.value})"
        ),
        actor=user,
        resource_type=str(body.resourceType),
        resource_id=body.resourceId,
        resource_label=label_of(body.resourceType, obj),
        request=request,
    )
    await session.commit()
    return {"mode": str(row.mode), "permission": str(row.permission), "token": row.token}


async def _decide_request(
    session: SessionDep,
    request_id: str,
    user: User,
    approve: bool,
    note: str | None,
    reason: str | None,
    http_request: Request,
) -> dict[str, Any]:
    entry = await session.get(AccessRequest, request_id)
    if entry is None:
        raise NotFoundError("Yêu cầu")
    if entry.status != RequestStatus.PENDING:
        raise ValidationError("Yêu cầu này đã được xử lý.")

    if entry.resource_type and entry.resource_id:
        obj = await load(session, entry.resource_type, entry.resource_id)
        is_owner = getattr(obj, owner_field(entry.resource_type), None) == user.id
        if not is_owner and not user.is_admin:
            raise ForbiddenError("Chỉ chủ sở hữu hoặc Admin duyệt được yêu cầu này.")
    elif not user.is_admin:
        raise ForbiddenError("Chỉ Admin duyệt được yêu cầu này.")

    entry.status = RequestStatus.APPROVED if approve else RequestStatus.REJECTED
    entry.decided_by_id = user.id
    entry.decided_at = now()
    entry.decision_note = note or reason

    if approve:
        if entry.type == AccessRequestType.RESOURCE_ACCESS and entry.requester_id:
            existing = (
                await session.execute(
                    select(Share).where(
                        Share.resource_type == entry.resource_type,
                        Share.resource_id == entry.resource_id,
                        Share.user_id == entry.requester_id,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                existing.permission = entry.permission or Permission.VIEW
            else:
                session.add(
                    Share(
                        id=Share.make_id(),
                        resource_type=entry.resource_type,
                        resource_id=entry.resource_id,
                        user_id=entry.requester_id,
                        permission=entry.permission or Permission.VIEW,
                        created_by_id=user.id,
                    )
                )
        elif entry.type == AccessRequestType.ROLE_UPGRADE and entry.requester_id:
            requester = await session.get(User, entry.requester_id)
            if requester and entry.requested_role:
                requester.role = entry.requested_role
        elif entry.type == AccessRequestType.ACCOUNT:
            from app.services.provisioning import invite_user

            await invite_user(
                session,
                email=entry.requester_email,
                inviter=user,
            )

    if entry.requester_id:
        await push(
            session,
            user_id=entry.requester_id,
            kind=NotificationType.ACCESS_REQUEST,
            title="Yêu cầu được duyệt" if approve else "Yêu cầu bị từ chối",
            body=f"{entry.resource_label}: {note or reason or ''}".strip(),
            target_url=(
                target_url(entry.resource_type, entry.resource_id)
                if entry.resource_type and entry.resource_id
                else "/"
            ),
            actor_id=user.id,
        )

    await audit.record(
        session,
        action="access_request.approve" if approve else "access_request.reject",
        summary=(
            f"{user.name} {'duyệt' if approve else 'từ chối'} yêu cầu của "
            f"{entry.requester_name or entry.requester_email} vào {entry.resource_label}"
        ),
        actor=user,
        resource_type=str(entry.resource_type) if entry.resource_type else "account",
        resource_id=entry.resource_id,
        resource_label=entry.resource_label,
        meta={"requestId": entry.id, "reason": reason},
        request=http_request,
    )
    await session.commit()
    return serialize_request(entry)


@router.patch("/shares/{share_id}", summary="Đổi quyền")
async def update_share(
    share_id: str,
    body: SharePatch,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    row = await session.get(Share, share_id)
    if row is None:
        raise NotFoundError("Bản ghi chia sẻ")
    obj, _ = await require(session, user, row.resource_type, row.resource_id, Permission.EDIT)

    row.permission = body.permission
    await audit.record(
        session,
        action="share.update",
        summary=(
            f"{user.name} đổi quyền chia sẻ trên “{label_of(row.resource_type, obj)}” "
            f"thành {body.permission.value}"
        ),
        actor=user,
        resource_type=str(row.resource_type),
        resource_id=row.resource_id,
        resource_label=label_of(row.resource_type, obj),
        request=request,
    )
    await session.commit()
    return {"id": row.id, "permission": str(row.permission)}


@router.delete("/shares/{share_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Gỡ quyền")
async def delete_share(
    share_id: str, user: CurrentUser, session: SessionDep, request: Request
) -> None:
    row = await session.get(Share, share_id)
    if row is None:
        raise NotFoundError("Bản ghi chia sẻ")
    obj, _ = await require(session, user, row.resource_type, row.resource_id, Permission.EDIT)

    await audit.record(
        session,
        action="share.revoke",
        summary=f"{user.name} gỡ quyền chia sẻ trên “{label_of(row.resource_type, obj)}”",
        actor=user,
        resource_type=str(row.resource_type),
        resource_id=row.resource_id,
        resource_label=label_of(row.resource_type, obj),
        request=request,
    )
    await session.delete(row)
    await session.commit()


@router.post("/access-requests/{request_id}/approve", summary="Duyệt yêu cầu cấp quyền")
async def approve_request(
    request_id: str,
    body: DecisionBody,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    return await _decide_request(session, request_id, user, True, body.note, None, request)


@router.post("/access-requests/{request_id}/reject", summary="Từ chối yêu cầu cấp quyền")
async def reject_request(
    request_id: str,
    body: RejectBody,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    return await _decide_request(session, request_id, user, False, None, body.reason, request)


@router.get("/access-requests", summary="Yêu cầu tôi đã gửi")
async def my_requests(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    rows = (
        (
            await session.execute(
                select(AccessRequest)
                .where(
                    or_(
                        AccessRequest.requester_id == user.id,
                        AccessRequest.requester_email == user.email,
                    )
                )
                .order_by(AccessRequest.created_at.desc())
            )
        )
        .unique()
        .scalars()
        .all()
    )
    return {"items": [serialize_request(r) for r in rows], "total": len(rows)}


@router.get("/directory", summary="Tìm người và nhóm để chia sẻ / bàn giao")
async def directory(
    user: CurrentUser,
    session: SessionDep,
    q: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 8,
    includeTeams: Annotated[bool, Query()] = True,
) -> dict[str, Any]:
    stmt = select(User).where(User.id != user.id, User.status != "locked")
    if q:
        term = search_term(q)

        stmt = stmt.where(
            or_(
                User.name_norm.ilike(term),
                User.handle.ilike(term),
                User.email.ilike(term),
            )
        )
    people = (await session.execute(stmt.order_by(User.name_norm).limit(limit))).scalars().all()

    items: list[dict[str, Any]] = [directory_entry(p) for p in people]

    if includeTeams:
        team_stmt = select(Team)
        if q:
            team_stmt = team_stmt.where(Team.name_norm.ilike(search_term(q)))
        teams = (
            (await session.execute(team_stmt.order_by(Team.name_norm).limit(limit))).scalars().all()
        )
        for team in teams:
            count = int(
                (
                    await session.execute(
                        select(func.count()).select_from(User).where(User.team_id == team.id)
                    )
                ).scalar_one()
                or 0
            )
            items.append(team_entry(team, count))

    return {"items": items, "total": len(items)}


@router.get("/shares/mine", summary="Tài nguyên được chia sẻ với tôi")
async def shared_with_me(
    user: CurrentUser,
    session: SessionDep,
    resourceType: Annotated[ResourceType | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(Share).where(
        or_(
            Share.user_id == user.id,
            Share.team_id == user.team_id if user.team_id else Share.team_id.is_(None) & False,
        )
    )
    if resourceType:
        stmt = stmt.where(Share.resource_type == resourceType)

    rows = (await session.execute(stmt.order_by(Share.created_at.desc()))).unique().scalars().all()

    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            obj = await load(session, row.resource_type, row.resource_id)
        except NotFoundError:
            continue
        items.append(
            {
                "shareId": row.id,
                "resourceType": str(row.resource_type),
                "resourceId": row.resource_id,
                "label": label_of(row.resource_type, obj),
                "permission": str(row.permission),
                "scope": str(getattr(obj, "scope", Scope.MINE)),
                "url": target_url(row.resource_type, row.resource_id),
                "sharedAt": row.created_at,
            }
        )
    return {"items": items, "total": len(items)}
