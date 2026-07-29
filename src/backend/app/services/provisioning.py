from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.core.security import now, share_link_token
from app.core.text import handle_of, initials, normalize
from app.db.models.enums import NotificationType, Role, UserStatus
from app.db.models.user import Invite, Team, User, UserNotificationSettings
from app.integrations.resend_client import resend
from app.services.auth import assert_domain_allowed
from app.services.notifications import push

log = get_logger(__name__)

AVATAR_COLORS = [
    "#0068FF",
    "#1A30A0",
    "#12A150",
    "#D9483B",
    "#787CA6",
    "#4d5f85",
    "#021F4E",
]


def pick_avatar_color(seed: str) -> str:
    return AVATAR_COLORS[sum(seed.encode()) % len(AVATAR_COLORS)]


async def invite_user(
    session: AsyncSession,
    *,
    email: str,
    inviter: User,
    name: str | None = None,
    role: Role = Role.MEMBER,
    team_id: str | None = None,
) -> tuple[User, Invite]:
    email = email.strip().lower()
    assert_domain_allowed(email)

    existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing and existing.status != UserStatus.INVITED:
        raise ConflictError(f"{email} đã có tài khoản trong hệ thống.")

    display = handle_of(email)
    if existing:
        user = existing
        user.role = role
        user.team_id = team_id or user.team_id
    else:
        user = User(
            id=User.make_id(),
            email=email,
            handle=handle_of(email),
            name=display,
            name_norm=normalize(display),
            initials=initials(handle_of(email)),
            avatar_color=pick_avatar_color(email),
            role=role,
            status=UserStatus.INVITED,
            team_id=team_id,
            invited_by_id=inviter.id,
            is_first_login=True,
        )
        session.add(user)
        await session.flush()
        session.add(UserNotificationSettings(user_id=user.id))

    invite = Invite(
        id=Invite.make_id(),
        email=email,
        role=role,
        team_id=team_id,
        token=share_link_token(),
        invited_by_id=inviter.id,
        expires_at=now() + timedelta(days=7),
        meta={"name": display},
    )
    session.add(invite)
    await session.flush()

    team_name: str | None = None
    if team_id:
        team = await session.get(Team, team_id)
        team_name = team.name if team else None

    await push(
        session,
        user_id=user.id,
        kind=NotificationType.INVITE,
        title="Chào mừng tới ZB Agent",
        body=f"{inviter.name} đã mời bạn với vai trò {role.value}.",
        target_url="/",
        actor_id=inviter.id,
    )

    try:
        sent = await resend.send_invite(
            email,
            inviter_name=inviter.name,
            role=role.value,
            accept_url=f"/?invite={invite.token}",
            team=team_name,
        )
        log.info(
            "invite_email",
            email=email,
            delivered=sent.delivered,
            dry_run=sent.dry_run,
            redirected_to=sent.to if sent.redirected_from else None,
        )
    except Exception as exc:
        log.warning("invite_email_failed", email=email, error=str(exc))

    return user, invite


def role_matrix(overrides: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    from app.services.auth import DEFAULT_PERMISSIONS

    overrides = overrides or {}
    return [
        {
            "role": str(role),
            "label": "Quản trị viên" if role == Role.ADMIN else "Thành viên",
            "permissions": overrides.get(str(role)) or defaults,
            "isDefault": str(role) not in overrides,
        }
        for role, defaults in DEFAULT_PERMISSIONS.items()
    ]


__all__ = ["AVATAR_COLORS", "invite_user", "pick_avatar_color", "role_matrix", "settings"]
