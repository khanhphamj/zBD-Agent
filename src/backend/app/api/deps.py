from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import decode_token
from app.db.models.enums import UserStatus
from app.db.models.user import User
from app.db.session import get_session

bearer = HTTPBearer(auto_error=False, description="Bearer token from POST /auth/otp/verify")

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> User:
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("Thiếu token xác thực.")

    payload = decode_token(credentials.credentials, expect="access")
    user = (
        await session.execute(
            select(User)
            .where(User.id == payload["sub"])
            .options(selectinload(User.team), selectinload(User.notification_settings))
        )
    ).scalar_one_or_none()

    if user is None:
        raise UnauthorizedError("Người dùng không còn tồn tại.")
    if user.status == UserStatus.LOCKED:
        raise ForbiddenError("Tài khoản đã bị khoá. Liên hệ Admin để mở lại.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_admin_user(user: CurrentUser) -> User:
    if not user.is_admin:
        raise ForbiddenError("Chỉ Admin mới truy cập được mục Quản trị.")
    return user


AdminUser = Annotated[User, Depends(get_admin_user)]


async def get_optional_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> User | None:
    if credentials is None or not credentials.credentials:
        return None
    try:
        return await get_current_user(session, credentials)
    except (UnauthorizedError, ForbiddenError):
        return None


OptionalUser = Annotated[User | None, Depends(get_optional_user)]


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


async def last_event_id(
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> int:
    try:
        return int(last_event_id) if last_event_id else 0
    except ValueError:
        return 0


LastEventId = Annotated[int, Depends(last_event_id)]
