import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt

from app.core.config import settings
from app.core.errors import UnauthorizedError

TokenType = Literal["access", "refresh"]


def now() -> datetime:
    return datetime.now(UTC)


def _encode(
    subject: str, token_type: TokenType, ttl: timedelta, **claims: Any
) -> tuple[str, datetime]:
    issued = now()
    expires = issued + ttl
    payload: dict[str, Any] = {
        "sub": subject,
        "typ": token_type,
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": uuid.uuid4().hex,
        **claims,
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, expires


def create_access_token(user_id: str, role: str, email: str) -> tuple[str, datetime]:
    return _encode(
        user_id,
        "access",
        timedelta(minutes=settings.access_token_ttl_minutes),
        role=role,
        email=email,
    )


def create_refresh_token(user_id: str) -> tuple[str, datetime]:
    return _encode(user_id, "refresh", timedelta(days=settings.refresh_token_ttl_days))


def decode_token(token: str, expect: TokenType = "access") -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Phiên đăng nhập đã hết hạn.") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Token không hợp lệ.") from exc

    if payload.get("typ") != expect:
        raise UnauthorizedError("Loại token không đúng.")
    if not payload.get("sub"):
        raise UnauthorizedError("Token thiếu thông tin người dùng.")
    return payload


def generate_otp(length: int | None = None) -> str:
    n = length or settings.otp_length
    return "".join(secrets.choice("0123456789") for _ in range(n))


def hash_otp(code: str, email: str) -> str:
    return hmac.new(
        settings.secret_key.encode(),
        f"{email.strip().lower()}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def verify_otp(code: str, email: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_otp(code, email), hashed)


def share_link_token() -> str:
    return secrets.token_urlsafe(24)


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def public_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"
