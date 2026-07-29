from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import AdminUser, SessionDep
from app.core.errors import NotFoundError, ValidationError
from app.core.pagination import PageParams, page_params, paginate
from app.core.security import now
from app.core.text import search_term
from app.db.models.admin import AiConfig, SystemPolicy
from app.db.models.collab import AuditLog
from app.db.models.enums import AccessRequestType, RequestStatus, Role, UserStatus
from app.db.models.user import AccessRequest, RolePermissions, Team, User
from app.schemas.requests import (
    AdminUserPatch,
    AiConfigPatch,
    DecisionBody,
    InviteCreate,
    PolicyPatch,
    RejectBody,
    RolePermissionsPatch,
)
from app.schemas.serializers import (
    access_request as serialize_request,
)
from app.schemas.serializers import (
    admin_user,
    audit_detail,
    audit_row,
)
from app.services import audit
from app.services.auth import revoke_all_refresh
from app.services.provisioning import invite_user, role_matrix

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", summary="Danh sách người dùng")
async def list_users(
    admin: AdminUser,
    session: SessionDep,
    q: Annotated[str | None, Query()] = None,
    role: Annotated[Role | None, Query()] = None,
    team: Annotated[str | None, Query()] = None,
    status_filter: Annotated[UserStatus | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    stmt = select(User).options(selectinload(User.team))
    if role:
        stmt = stmt.where(User.role == role)
    if team:
        stmt = stmt.where(
            or_(User.team_id == team, User.team_id.in_(select(Team.id).where(Team.name == team)))
        )
    if status_filter:
        stmt = stmt.where(User.status == status_filter)
    if q:
        term = search_term(q)
        stmt = stmt.where(
            or_(User.name_norm.ilike(term), User.handle.ilike(term), User.email.ilike(term))
        )

    rows = (await session.execute(stmt.order_by(User.name_norm))).unique().scalars().all()
    teams = (await session.execute(select(Team).order_by(Team.name_norm))).scalars().all()
    return {
        "items": [admin_user(u) for u in rows],
        "total": len(rows),
        "teams": [{"id": t.id, "name": t.name} for t in teams],
    }


@router.post("/users/invite", status_code=status.HTTP_201_CREATED, summary="Mời thành viên")
async def invite(
    body: InviteCreate, admin: AdminUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    user, invite_row = await invite_user(
        session,
        email=body.email,
        inviter=admin,
        role=body.role,
        team_id=body.teamId,
    )
    await audit.record(
        session,
        action="invite.create",
        summary=f"{admin.name} mời {body.email} với vai trò {body.role.value}",
        actor=admin,
        resource_type="user",
        resource_id=user.id,
        resource_label=body.email,
        meta={"inviteId": invite_row.id, "role": str(body.role)},
        request=request,
    )
    await session.commit()
    await session.refresh(user)
    return {**admin_user(user), "inviteId": invite_row.id, "expiresAt": invite_row.expires_at}


@router.patch("/users/{user_id}", summary="Đổi vai trò hoặc nhóm")
async def update_user(
    user_id: str,
    body: AdminUserPatch,
    admin: AdminUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    target = await session.get(User, user_id)
    if target is None:
        raise NotFoundError("Người dùng")

    if body.role is not None and body.role != target.role:
        if target.role == Role.ADMIN and body.role != Role.ADMIN:
            remaining = (
                (
                    await session.execute(
                        select(User).where(User.role == Role.ADMIN, User.id != target.id)
                    )
                )
                .scalars()
                .all()
            )
            if not remaining:
                raise ValidationError("Hệ thống phải còn ít nhất một Admin.", field="role")
        target.role = body.role

    if body.teamId is not None:
        target.team_id = body.teamId or None
    if body.title is not None:
        target.title = body.title

    await audit.record(
        session,
        action="user.update",
        summary=f"{admin.name} cập nhật {target.email}",
        actor=admin,
        resource_type="user",
        resource_id=target.id,
        resource_label=target.email,
        meta={"role": str(target.role), "teamId": target.team_id},
        request=request,
    )
    await session.commit()
    await session.refresh(target)
    return admin_user(target)


@router.post("/users/{user_id}/lock", summary="Khoá tài khoản")
async def lock_user(
    user_id: str, admin: AdminUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    target = await session.get(User, user_id)
    if target is None:
        raise NotFoundError("Người dùng")
    if target.id == admin.id:
        raise ValidationError("Không thể tự khoá tài khoản của mình.")

    target.status = UserStatus.LOCKED

    await revoke_all_refresh(session, target.id)

    await audit.record(
        session,
        action="user.lock",
        summary=f"{admin.name} khoá tài khoản {target.email}",
        actor=admin,
        resource_type="user",
        resource_id=target.id,
        resource_label=target.email,
        request=request,
    )
    await session.commit()
    await session.refresh(target)
    return admin_user(target)


@router.post("/users/{user_id}/unlock", summary="Mở khoá tài khoản")
async def unlock_user(
    user_id: str, admin: AdminUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    target = await session.get(User, user_id)
    if target is None:
        raise NotFoundError("Người dùng")
    target.status = UserStatus.ACTIVE
    target.locked_until = None
    await audit.record(
        session,
        action="user.unlock",
        summary=f"{admin.name} mở khoá tài khoản {target.email}",
        actor=admin,
        resource_type="user",
        resource_id=target.id,
        resource_label=target.email,
        request=request,
    )
    await session.commit()
    await session.refresh(target)
    return admin_user(target)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Xoá người dùng")
async def delete_user(
    user_id: str, admin: AdminUser, session: SessionDep, request: Request
) -> None:
    target = await session.get(User, user_id)
    if target is None:
        raise NotFoundError("Người dùng")
    if target.id == admin.id:
        raise ValidationError("Không thể tự xoá tài khoản của mình.")

    from app.db.models.customer import Customer

    owned = (
        await session.execute(select(Customer.id).where(Customer.owner_id == target.id).limit(1))
    ).scalar_one_or_none()
    if owned:
        raise ValidationError(
            "Người dùng còn đang phụ trách khách hàng. Hãy bàn giao trước khi xoá."
        )

    await audit.record(
        session,
        action="user.delete",
        summary=f"{admin.name} xoá người dùng {target.email}",
        actor=admin,
        resource_type="user",
        resource_id=user_id,
        resource_label=target.email,
        request=request,
    )
    await session.delete(target)
    await session.commit()


@router.get("/roles", summary="Ma trận quyền theo vai trò")
async def get_roles(admin: AdminUser, session: SessionDep) -> dict[str, Any]:
    rows = (await session.execute(select(RolePermissions))).scalars().all()
    overrides = {str(r.role): list(r.permissions) for r in rows if r.permissions}
    return {"items": role_matrix(overrides)}


@router.patch("/roles/{role}", summary="Cập nhật quyền của vai trò")
async def update_role(
    role: Role,
    body: RolePermissionsPatch,
    admin: AdminUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    row = await session.get(RolePermissions, role)
    if row is None:
        row = RolePermissions(role=role, permissions=body.permissions, updated_by_id=admin.id)
        session.add(row)
    else:
        row.permissions = body.permissions
        row.updated_by_id = admin.id

    await audit.record(
        session,
        action="role.update",
        summary=f"{admin.name} cập nhật quyền vai trò {role.value}",
        actor=admin,
        resource_type="role",
        resource_id=str(role),
        resource_label=role.value,
        meta={"permissions": body.permissions},
        request=request,
    )
    await session.commit()
    return {"role": str(role), "permissions": row.permissions}


@router.get("/requests", summary="Yêu cầu chờ xử lý — gộp cả 4 loại")
async def list_requests(
    admin: AdminUser,
    session: SessionDep,
    type_filter: Annotated[AccessRequestType | None, Query(alias="type")] = None,
    status_filter: Annotated[RequestStatus | None, Query(alias="status")] = RequestStatus.PENDING,
) -> dict[str, Any]:
    stmt = select(AccessRequest).options(selectinload(AccessRequest.requester))
    if type_filter:
        stmt = stmt.where(AccessRequest.type == type_filter)
    if status_filter:
        stmt = stmt.where(AccessRequest.status == status_filter)

    rows = (
        (await session.execute(stmt.order_by(AccessRequest.created_at.desc())))
        .unique()
        .scalars()
        .all()
    )
    counts: dict[str, int] = {}
    for row in rows:
        counts[str(row.type)] = counts.get(str(row.type), 0) + 1
    return {
        "items": [serialize_request(r) for r in rows],
        "total": len(rows),
        "countsByType": counts,
    }


@router.post("/requests/{request_id}/approve", summary="Duyệt yêu cầu")
async def approve_request(
    request_id: str,
    body: DecisionBody,
    admin: AdminUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    from app.api.v1.shares import _decide_request

    return await _decide_request(session, request_id, admin, True, body.note, None, request)


@router.post("/requests/{request_id}/reject", summary="Từ chối yêu cầu")
async def reject_request(
    request_id: str,
    body: RejectBody,
    admin: AdminUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    from app.api.v1.shares import _decide_request

    return await _decide_request(session, request_id, admin, False, None, body.reason, request)


@router.get("/audit", summary="Audit log")
async def list_audit(
    admin: AdminUser,
    session: SessionDep,
    params: Annotated[PageParams, Depends(page_params)],
    category: Annotated[str | None, Query()] = None,
    actor: Annotated[str | None, Query()] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(AuditLog).options(selectinload(AuditLog.actor))
    if category:
        stmt = stmt.where(AuditLog.category == category)
    if actor:
        stmt = stmt.where(
            or_(
                AuditLog.actor_id == actor,
                AuditLog.actor_id.in_(select(User.id).where(User.handle == actor)),
            )
        )
    if from_:
        stmt = stmt.where(AuditLog.created_at >= from_)
    if to:
        stmt = stmt.where(AuditLog.created_at <= to)
    if params.q:
        stmt = stmt.where(AuditLog.search_norm.ilike(search_term(params.q)))

    stmt = stmt.order_by(AuditLog.created_at.desc())
    page = await paginate(session, stmt, params, audit_row)
    return {
        **page,
        "categories": [
            c.value
            for c in __import__("app.db.models.enums", fromlist=["AuditCategory"]).AuditCategory
        ],
    }


@router.get("/audit/{audit_id}", summary="Chi tiết một bản ghi audit")
async def get_audit(audit_id: str, admin: AdminUser, session: SessionDep) -> dict[str, Any]:
    row = await session.get(AuditLog, audit_id)
    if row is None:
        raise NotFoundError("Bản ghi audit")
    await session.refresh(row)
    return audit_detail(row)


async def _ai_config(session: SessionDep) -> AiConfig:
    row = await session.get(AiConfig, 1)
    if row is None:
        row = AiConfig(id=1)
        session.add(row)
        await session.flush()
    return row


@router.get("/ai-config", summary="Cấu hình AI")
async def get_ai_config(admin: AdminUser, session: SessionDep) -> dict[str, Any]:
    row = await _ai_config(session)
    await session.commit()
    return _serialize_ai(row)


def _serialize_ai(row: AiConfig) -> dict[str, Any]:
    return {
        "model": row.model,
        "visionModel": row.vision_model,
        "temperature": row.temperature,
        "maxTokens": row.max_tokens,
        "researchDepth": str(row.research_depth),
        "autoApproveThreshold": row.auto_approve_threshold,
        "monthlyBudgetUsd": row.monthly_budget_usd,
        "monthSpendUsd": round(row.month_spend_usd, 2),
        "updatedAt": row.updated_at,
    }


@router.patch("/ai-config", summary="Cập nhật cấu hình AI")
async def update_ai_config(
    body: AiConfigPatch, admin: AdminUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    row = await _ai_config(session)
    changes: dict[str, Any] = {}
    for field, wire, value in (
        ("model", "model", body.model),
        ("vision_model", "visionModel", body.visionModel),
        ("temperature", "temperature", body.temperature),
        ("max_tokens", "maxTokens", body.maxTokens),
        ("research_depth", "researchDepth", body.researchDepth),
        ("auto_approve_threshold", "autoApproveThreshold", body.autoApproveThreshold),
        ("monthly_budget_usd", "monthlyBudgetUsd", body.monthlyBudgetUsd),
    ):
        if value is not None:
            setattr(row, field, value)
            changes[wire] = str(value) if not isinstance(value, int | float | bool) else value
    row.updated_by_id = admin.id

    await audit.record(
        session,
        action="system.ai_config",
        summary=f"{admin.name} cập nhật cấu hình AI",
        actor=admin,
        resource_type="ai_config",
        resource_id="1",
        meta=changes,
        request=request,
    )
    await session.commit()
    await session.refresh(row)
    return _serialize_ai(row)


async def _policy(session: SessionDep) -> SystemPolicy:
    row = await session.get(SystemPolicy, 1)
    if row is None:
        row = SystemPolicy(id=1)
        session.add(row)
        await session.flush()
    return row


def _serialize_policy(row: SystemPolicy) -> dict[str, Any]:
    return {
        "allowCompanyLink": row.allow_company_link,
        "requireAdminApprovalPublish": row.require_admin_approval_publish,
        "blockExternalShare": row.block_external_share,
        "updatedAt": row.updated_at,
    }


@router.get("/ai-profiles", summary="Tham số MaaS theo từng tác vụ (harness layer)")
async def ai_profiles(admin: AdminUser, session: SessionDep) -> dict[str, Any]:
    from app.integrations.harness import describe, resolve

    cfg = await _ai_config(session)
    await session.commit()
    return {
        "defaults": describe(),
        "effective": [
            {
                "key": d["key"],
                "model": r.model,
                "maxTokens": r.max_tokens,
                "temperature": r.temperature,
                "topP": r.top_p,
                "timeoutSeconds": r.timeout,
                "jsonMode": r.json_mode,
            }
            for d in describe()
            for r in [resolve(str(d["key"]), admin=cfg)]
        ],
    }


@router.get("/policies", summary="Chính sách hệ thống")
async def get_policies(admin: AdminUser, session: SessionDep) -> dict[str, Any]:
    row = await _policy(session)
    await session.commit()
    return _serialize_policy(row)


@router.patch("/policies", summary="Cập nhật chính sách hệ thống")
async def update_policies(
    body: PolicyPatch, admin: AdminUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    row = await _policy(session)
    changes: dict[str, Any] = {}
    for field, wire, value in (
        ("allow_company_link", "allowCompanyLink", body.allowCompanyLink),
        (
            "require_admin_approval_publish",
            "requireAdminApprovalPublish",
            body.requireAdminApprovalPublish,
        ),
        ("block_external_share", "blockExternalShare", body.blockExternalShare),
    ):
        if value is not None:
            setattr(row, field, value)
            changes[wire] = value
    row.updated_by_id = admin.id

    await audit.record(
        session,
        action="system.policies",
        summary=f"{admin.name} cập nhật chính sách hệ thống",
        actor=admin,
        resource_type="system_policy",
        resource_id="1",
        meta=changes,
        request=request,
    )
    await session.commit()
    await session.refresh(row)
    return _serialize_policy(row)


@router.get("/teams", summary="Danh sách nhóm")
async def list_teams(admin: AdminUser, session: SessionDep) -> dict[str, Any]:
    rows = (await session.execute(select(Team).order_by(Team.name_norm))).scalars().all()
    return {
        "items": [
            {"id": t.id, "name": t.name, "description": t.description, "createdAt": t.created_at}
            for t in rows
        ],
        "total": len(rows),
    }


@router.get("/stats", summary="Tổng quan hệ thống")
async def admin_stats(admin: AdminUser, session: SessionDep) -> dict[str, Any]:
    from sqlalchemy import func as sqlfunc

    from app.db.models.customer import Customer
    from app.db.models.job import Job
    from app.db.models.knowledge import KnowledgeBase
    from app.db.models.proposal import Proposal

    async def count(model: Any, *where: Any) -> int:
        return int(
            (
                await session.execute(select(sqlfunc.count()).select_from(model).where(*where))
            ).scalar_one()
            or 0
        )

    return {
        "users": await count(User),
        "activeUsers": await count(User, User.status == UserStatus.ACTIVE),
        "lockedUsers": await count(User, User.status == UserStatus.LOCKED),
        "customers": await count(Customer),
        "proposals": await count(Proposal),
        "knowledgeBases": await count(KnowledgeBase),
        "pendingRequests": await count(
            AccessRequest, AccessRequest.status == RequestStatus.PENDING
        ),
        "jobsRunning": await count(Job, Job.status.in_(["queued", "running"])),
        "generatedAt": now(),
    }
