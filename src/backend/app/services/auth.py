from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.errors import (
    AuthDomainError,
    AuthServiceError,
    AuthUnknownError,
    OtpExpiredError,
    OtpInvalidError,
    OtpLockedError,
    OtpResendTooSoonError,
)
from app.core.logging import get_logger
from app.core.security import (
    content_hash,
    create_access_token,
    create_refresh_token,
    generate_otp,
    hash_otp,
    now,
    verify_otp,
)
from app.core.text import domain_of
from app.db.models.enums import Role, UserStatus
from app.db.models.user import OtpChallenge, RefreshToken, User
from app.integrations.resend_client import EmailError, resend

log = get_logger(__name__)


DEFAULT_PERMISSIONS: dict[Role, list[str]] = {
    Role.ADMIN: [
        "customer:read",
        "customer:write",
        "customer:delete",
        "customer:handover",
        "research:run",
        "proposal:read",
        "proposal:write",
        "proposal:delete",
        "proposal:approve",
        "proposal:export",
        "knowledge:read",
        "knowledge:write",
        "knowledge:delete",
        "skill:read",
        "skill:write",
        "skill:delete",
        "template:read",
        "template:write",
        "template:delete",
        "product:read",
        "product:write",
        "share:manage",
        "admin:users",
        "admin:roles",
        "admin:requests",
        "admin:audit",
        "admin:ai",
        "admin:policies",
    ],
    Role.MEMBER: [
        "customer:read",
        "customer:write",
        "customer:handover",
        "research:run",
        "proposal:read",
        "proposal:write",
        "proposal:export",
        "knowledge:read",
        "knowledge:write",
        "skill:read",
        "skill:write",
        "template:read",
        "template:write",
        "product:read",
        "share:manage",
    ],
}


def assert_domain_allowed(email: str) -> None:
    if domain_of(email) not in settings.allowed_email_domains:
        raise AuthDomainError(settings.allowed_email_domains)


async def find_user(session: AsyncSession, email: str) -> User | None:
    return (
        await session.execute(
            select(User)
            .where(User.email == email.strip().lower())
            .options(selectinload(User.team), selectinload(User.notification_settings))
        )
    ).scalar_one_or_none()


async def latest_challenge(session: AsyncSession, email: str) -> OtpChallenge | None:
    return (
        await session.execute(
            select(OtpChallenge)
            .where(OtpChallenge.email == email, OtpChallenge.consumed_at.is_(None))
            .order_by(OtpChallenge.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _assert_not_locked(challenge: OtpChallenge | None) -> None:
    if challenge and challenge.locked_until and challenge.locked_until > now():
        remaining = max(1, int((challenge.locked_until - now()).total_seconds() // 60) + 1)
        raise OtpLockedError(challenge.locked_until.isoformat(), remaining)


async def request_otp(
    session: AsyncSession,
    email: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    is_resend: bool = False,
) -> dict[str, int]:
    email = email.strip().lower()
    assert_domain_allowed(email)

    user = await find_user(session, email)
    if user is None:
        raise AuthUnknownError(email)
    if user.status == UserStatus.LOCKED:
        raise AuthServiceError("Tài khoản đã bị khoá. Liên hệ Admin để mở lại.")

    challenge = await latest_challenge(session, email)
    _assert_not_locked(challenge)

    if is_resend and challenge:
        elapsed = (now() - challenge.last_sent_at).total_seconds()
        if elapsed < settings.otp_resend_after_seconds:
            raise OtpResendTooSoonError(int(settings.otp_resend_after_seconds - elapsed))

    dev_code = settings.otp_dev_code_active
    code = settings.otp_dev_code if dev_code else generate_otp()
    expires = now() + timedelta(seconds=settings.otp_ttl_seconds)

    if challenge and not is_resend and challenge.expires_at > now():
        challenge.code_hash = hash_otp(code, email)
        challenge.expires_at = expires
        challenge.last_sent_at = now()
        challenge.send_count += 1
    elif challenge and is_resend:
        challenge.code_hash = hash_otp(code, email)
        challenge.expires_at = expires
        challenge.last_sent_at = now()
        challenge.send_count += 1
    else:
        challenge = OtpChallenge(
            id=OtpChallenge.make_id(),
            email=email,
            code_hash=hash_otp(code, email),
            expires_at=expires,
            last_sent_at=now(),
            ip=ip,
            user_agent=user_agent,
        )
        session.add(challenge)

    await session.commit()

    if dev_code:
        log.warning("otp_dev_code", email=email, code=code, env=settings.app_env)
        return {
            "ttlSeconds": settings.otp_ttl_seconds,
            "resendAfterSeconds": settings.otp_resend_after_seconds,
            "devCode": code,
            "channel": "dev",
        }

    try:
        await resend.send_otp(email, code, settings.otp_ttl_seconds)
    except EmailError as exc:
        log.warning("otp_email_failed", email=email, error=str(exc))
        raise AuthServiceError("Không gửi được mã xác thực. Vui lòng thử lại.") from exc

    return {
        "ttlSeconds": settings.otp_ttl_seconds,
        "resendAfterSeconds": settings.otp_resend_after_seconds,
        "channel": "email",
    }


async def verify_and_issue(
    session: AsyncSession,
    email: str,
    code: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, object]:
    email = email.strip().lower()
    assert_domain_allowed(email)

    challenge = await latest_challenge(session, email)
    _assert_not_locked(challenge)

    if challenge is None:
        raise OtpExpiredError

    if challenge.expires_at <= now():
        raise OtpExpiredError

    if not verify_otp(code, email, challenge.code_hash):
        challenge.attempts += 1
        attempts_left = max(0, settings.otp_max_attempts - challenge.attempts)
        if challenge.attempts >= settings.otp_max_attempts:
            challenge.locked_until = now() + timedelta(minutes=settings.otp_lock_minutes)
            await session.commit()
            raise OtpLockedError(challenge.locked_until.isoformat(), settings.otp_lock_minutes)
        await session.commit()
        raise OtpInvalidError(attempts_left)

    user = await find_user(session, email)
    if user is None:
        raise AuthUnknownError(email)

    challenge.consumed_at = now()
    return await issue_session(session, user, ip=ip, user_agent=user_agent)


async def issue_session(
    session: AsyncSession,
    user: User,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, object]:
    was_first = user.is_first_login
    user.is_first_login = False
    user.last_login_at = now()
    if user.status == UserStatus.INVITED:
        user.status = UserStatus.ACTIVE

    access, access_expires = create_access_token(user.id, str(user.role), user.email)
    refresh, refresh_expires = create_refresh_token(user.id)

    session.add(
        RefreshToken(
            id=RefreshToken.make_id(),
            user_id=user.id,
            token_hash=content_hash(refresh.encode()),
            expires_at=refresh_expires,
            ip=ip,
            user_agent=user_agent,
        )
    )
    await session.commit()

    return {
        "token": access,
        "refreshToken": refresh,
        "expiresAt": access_expires,
        "isFirstLogin": was_first,
        "needsOnboarding": user.needs_onboarding,
        "needsTour": user.needs_tour,
        "user": user,
    }


async def rotate_refresh(
    session: AsyncSession,
    user: User,
    old_token: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, object]:
    digest = content_hash(old_token.encode())
    row = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == digest))
    ).scalar_one_or_none()
    if row and row.revoked_at is None:
        row.revoked_at = now()

    access, access_expires = create_access_token(user.id, str(user.role), user.email)
    refresh, refresh_expires = create_refresh_token(user.id)
    session.add(
        RefreshToken(
            id=RefreshToken.make_id(),
            user_id=user.id,
            token_hash=content_hash(refresh.encode()),
            expires_at=refresh_expires,
            ip=ip,
            user_agent=user_agent,
        )
    )
    await session.commit()
    return {"token": access, "refreshToken": refresh, "expiresAt": access_expires}


async def revoke_all_refresh(session: AsyncSession, user_id: str) -> int:
    rows = (
        (
            await session.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.revoked_at = now()
    await session.commit()
    return len(rows)


async def permissions_for(session: AsyncSession, user: User) -> list[str]:
    from app.db.models.user import RolePermissions

    row = await session.get(RolePermissions, user.role)
    if row and row.permissions:
        return list(row.permissions)
    return DEFAULT_PERMISSIONS.get(user.role, [])
