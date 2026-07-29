from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, status
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep, client_ip
from app.core.config import settings
from app.core.errors import AuthUnknownError, ForbiddenError, UnauthorizedError, ValidationError
from app.core.logging import get_logger
from app.core.security import decode_token
from app.core.text import handle_of
from app.db.models.enums import (
    AccessRequestType,
    NotificationType,
    RequestStatus,
    Role,
    UserStatus,
)
from app.db.models.user import AccessRequest, User
from app.schemas.requests import (
    AccessRequestCreate,
    OtpRequest,
    OtpVerify,
    RefreshRequest,
)
from app.schemas.serializers import access_request as serialize_request
from app.schemas.serializers import me as serialize_me
from app.services import audit
from app.services.auth import (
    assert_domain_allowed,
    find_user,
    issue_session,
    permissions_for,
    request_otp,
    revoke_all_refresh,
    rotate_refresh,
    verify_and_issue,
)
from app.services.notifications import push

log = get_logger(__name__)
router = APIRouter(tags=["auth"])


@router.post("/auth/otp/request", summary="Gửi mã OTP — hoặc vào thẳng nếu email được bypass")
async def otp_request(
    body: OtpRequest,
    request: Request,
    session: SessionDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Email nằm trong OTP_BYPASS_EMAILS thì cấp token luôn, không gửi mã.

    Bypass bị vô hiệu hoá khi APP_ENV=production (trừ khi bật hẳn
    ALLOW_OTP_BYPASS_IN_PRODUCTION), và mỗi lần dùng đều ghi audit.
    """
    email = body.email.strip().lower()

    if settings.is_otp_bypass(email):
        assert_domain_allowed(email)
        user = await find_user(session, email)
        if user is None:
            raise AuthUnknownError(email)
        if user.status == UserStatus.LOCKED:
            raise ForbiddenError("Tài khoản đã bị khoá. Liên hệ Admin để mở lại.")

        result = await issue_session(session, user, ip=client_ip(request), user_agent=user_agent)
        result.pop("user", None)

        log.warning(
            "otp_bypass_login",
            email=email,
            env=settings.app_env,
            ip=client_ip(request),
            detail="Đăng nhập không cần OTP qua OTP_BYPASS_EMAILS.",
        )
        await audit.record(
            session,
            action="auth.bypass_login",
            summary=f"{user.name} đăng nhập không cần OTP (email trong OTP_BYPASS_EMAILS)",
            actor=user,
            resource_type="user",
            resource_id=user.id,
            resource_label=user.email,
            meta={"bypass": True, "env": settings.app_env},
            request=request,
        )
        await session.commit()

        return {
            "direct": True,
            **result,
            "user": serialize_me(user, await permissions_for(session, user)),
        }

    return await request_otp(
        session,
        body.email,
        ip=client_ip(request),
        user_agent=user_agent,
    )


@router.post("/auth/otp/resend", summary="Gửi lại mã OTP")
async def otp_resend(
    body: OtpRequest,
    request: Request,
    session: SessionDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> dict[str, int]:
    return await request_otp(
        session,
        body.email,
        ip=client_ip(request),
        user_agent=user_agent,
        is_resend=True,
    )


@router.post("/auth/otp/verify", summary="Xác thực OTP và cấp token")
async def otp_verify(
    body: OtpVerify,
    request: Request,
    session: SessionDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    result = await verify_and_issue(
        session,
        body.email,
        body.code,
        ip=client_ip(request),
        user_agent=user_agent,
    )
    user: User = result.pop("user")  # type: ignore[assignment]

    await audit.record(
        session,
        action="auth.login",
        summary=f"{user.name} đăng nhập",
        actor=user,
        resource_type="user",
        resource_id=user.id,
        resource_label=user.name,
        request=request,
    )
    await session.commit()

    return {**result, "user": serialize_me(user, await permissions_for(session, user))}


@router.post("/auth/refresh", summary="Làm mới access token")
async def refresh(
    body: RefreshRequest,
    request: Request,
    session: SessionDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    payload = decode_token(body.refreshToken, expect="refresh")
    user = await session.get(User, payload["sub"])
    if user is None or user.status == UserStatus.LOCKED:
        raise UnauthorizedError("Tài khoản không còn hiệu lực.")

    return await rotate_refresh(
        session,
        user,
        body.refreshToken,
        ip=client_ip(request),
        user_agent=user_agent,
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Đăng xuất")
async def logout(user: CurrentUser, session: SessionDep, request: Request) -> None:
    await revoke_all_refresh(session, user.id)
    await audit.record(
        session,
        action="auth.logout",
        summary=f"{user.name} đăng xuất",
        actor=user,
        resource_type="user",
        resource_id=user.id,
        request=request,
    )
    await session.commit()


@router.post(
    "/access-requests",
    status_code=status.HTTP_201_CREATED,
    summary="Xin cấp quyền — tài khoản mới, quyền tài nguyên, hoặc nâng vai trò",
)
async def create_access_request(
    body: AccessRequestCreate,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    from app.api.deps import get_optional_user
    from app.services.shares import KIND_LABEL, label_of, load, target_url

    caller = await get_optional_user(session, await _bearer_from(request))

    if body.resourceType and body.resourceId:
        if caller is None:
            raise UnauthorizedError("Cần đăng nhập để xin quyền vào tài nguyên.")
        kind = AccessRequestType.RESOURCE_ACCESS
        obj = await load(session, body.resourceType, body.resourceId)
        label = label_of(body.resourceType, obj)
        email, name = caller.email, caller.name
    elif body.requestedRole:
        if caller is None:
            raise UnauthorizedError("Cần đăng nhập để xin nâng vai trò.")
        kind = AccessRequestType.ROLE_UPGRADE
        label, email, name = str(body.requestedRole), caller.email, caller.name
    else:
        if not body.email:
            raise ValidationError("Cần địa chỉ email để gửi yêu cầu.", field="email")
        assert_domain_allowed(body.email)
        kind = AccessRequestType.ACCOUNT
        label, email, name = (
            "Tài khoản ZB Agent",
            body.email.lower(),
            body.name or handle_of(body.email),
        )

    existing = (
        await session.execute(
            select(AccessRequest).where(
                AccessRequest.requester_email == email,
                AccessRequest.type == kind,
                AccessRequest.resource_type == body.resourceType,
                AccessRequest.resource_id == body.resourceId,
                AccessRequest.status == RequestStatus.PENDING,
            )
        )
    ).scalar_one_or_none()

    if existing:
        existing.reason = body.reason or existing.reason
        existing.permission = body.permission
        await session.commit()
        return serialize_request(existing)

    entry = AccessRequest(
        id=AccessRequest.make_id(),
        type=kind,
        requester_id=caller.id if caller else None,
        requester_email=email,
        requester_name=name,
        resource_type=body.resourceType,
        resource_id=body.resourceId,
        resource_label=label,
        permission=body.permission,
        requested_role=body.requestedRole,
        reason=body.reason,
    )
    session.add(entry)
    await session.flush()

    recipients: list[User] = []
    if body.resourceType and body.resourceId:
        from app.services.shares import owner_field

        owner_id = getattr(obj, owner_field(body.resourceType), None)
        owner = await session.get(User, owner_id) if owner_id else None
        if owner:
            recipients = [owner]
    if not recipients:
        recipients = list(
            (await session.execute(select(User).where(User.role == Role.ADMIN))).scalars().all()
        )

    for recipient in recipients:
        await push(
            session,
            user_id=recipient.id,
            kind=NotificationType.ACCESS_REQUEST,
            title="Yêu cầu cấp quyền mới",
            body=f"{name} xin quyền {body.permission.value} vào {label}.",
            target_url=(
                target_url(body.resourceType, body.resourceId)
                if body.resourceType and body.resourceId
                else "/admin/requests"
            ),
            actor_id=caller.id if caller else None,
            meta={"requestId": entry.id},
        )
        try:
            from app.integrations.resend_client import resend

            await resend.send_access_request(
                recipient.email,
                requester_name=name,
                requester_email=email,
                resource_label=(
                    f"{KIND_LABEL[body.resourceType]} “{label}”" if body.resourceType else label
                ),
                permission=body.permission.value,
                reason=body.reason,
                url="/admin/requests",
            )
        except Exception:
            pass

    await audit.record(
        session,
        action="access_request.create",
        summary=f"{name} xin quyền {body.permission.value} vào {label}",
        actor=caller,
        resource_type=str(body.resourceType) if body.resourceType else "account",
        resource_id=body.resourceId,
        resource_label=label,
        request=request,
    )
    await session.commit()
    return serialize_request(entry)


async def _bearer_from(request: Request) -> Any:
    from fastapi.security import HTTPAuthorizationCredentials

    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return HTTPAuthorizationCredentials(scheme=scheme, credentials=token)
