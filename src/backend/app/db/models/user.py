from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.base import Base, EnumStr, JSONType, PublicIdMixin, TimestampMixin
from app.db.models.enums import (
    AccessRequestType,
    Permission,
    RequestStatus,
    ResourceType,
    Role,
    UserStatus,
)

if TYPE_CHECKING:
    from app.db.models.customer import Customer


class Team(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "teams"
    __id_prefix__ = "tem"

    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    name_norm: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)

    members: Mapped[list["User"]] = relationship(back_populates="team")


class User(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "users"
    __id_prefix__ = "usr"

    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    handle: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    name_norm: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    initials: Mapped[str] = mapped_column(String(4), nullable=False, default="?")

    avatar_color: Mapped[str] = mapped_column(String(9), nullable=False, default="#0068FF")
    avatar_url: Mapped[str | None] = mapped_column(String(512))

    role: Mapped[Role] = mapped_column(
        EnumStr(Role, 16), nullable=False, default=Role.MEMBER, index=True
    )
    status: Mapped[UserStatus] = mapped_column(
        EnumStr(UserStatus, 16), nullable=False, default=UserStatus.ACTIVE, index=True
    )
    team_id: Mapped[str | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str | None] = mapped_column(String(180))
    phone: Mapped[str | None] = mapped_column(String(40))

    is_first_login: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    onboarding_steps: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    tour_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    interests: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)

    ONBOARDING_STEPS: ClassVar[tuple[str, ...]] = ("profile", "team", "interests")

    @property
    def needs_onboarding(self) -> bool:
        """Tín hiệu đáng tin để chạy luồng onboarding.

        Không dùng is_first_login: cờ đó bị xoá ngay khi cấp token, tức trước lúc
        người dùng thực sự xem xong onboarding — đóng tab giữa luồng là mất luôn.
        Ở đây xét việc đã hoàn tất chưa, nên gián đoạn bao nhiêu lần vẫn quay lại đúng.
        """
        if self.always_onboarding:
            return True
        done = set(self.onboarding_steps or ())
        return bool(set(self.ONBOARDING_STEPS) - done)

    @property
    def always_onboarding(self) -> bool:
        """Tài khoản dùng để xem lại luồng lần đầu: hoàn tất onboarding vẫn không tắt.

        Đặt ở đây một chỗ nên cả /me, /auth/otp/verify và refresh đều nhất quán —
        ba nơi đó cùng đọc needs_onboarding.
        """
        return self.email.strip().lower() in {
            e.strip().lower() for e in settings.always_onboarding_emails
        }

    @property
    def needs_tour(self) -> bool:
        return self.always_onboarding or not self.tour_completed

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invited_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    team: Mapped[Team | None] = relationship(back_populates="members", lazy="joined")
    notification_settings: Mapped["UserNotificationSettings | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    owned_customers: Mapped[list["Customer"]] = relationship(
        back_populates="owner", foreign_keys="Customer.owner_id"
    )

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def display_team(self) -> str | None:
        return self.team.name if self.team else None


class UserNotificationSettings(Base, TimestampMixin):
    __tablename__ = "user_notification_settings"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    email_on_research_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_on_approval_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_on_comment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_on_share: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user: Mapped[User] = relationship(back_populates="notification_settings")


class OtpChallenge(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "otp_challenges"
    __id_prefix__ = "otp"

    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    send_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))

    __table_args__ = (Index("ix_otp_email_created", "email", "created_at"),)


class RefreshToken(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "refresh_tokens"
    __id_prefix__ = "rft"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))

    user: Mapped[User] = relationship()


class AccessRequest(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "access_requests"
    __id_prefix__ = "req"

    type: Mapped[AccessRequestType] = mapped_column(
        EnumStr(AccessRequestType, 24), nullable=False, index=True
    )
    status: Mapped[RequestStatus] = mapped_column(
        EnumStr(RequestStatus, 16), nullable=False, default=RequestStatus.PENDING, index=True
    )

    requester_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    requester_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    requester_name: Mapped[str | None] = mapped_column(String(180))

    resource_type: Mapped[ResourceType | None] = mapped_column(
        EnumStr(ResourceType, 24), index=True
    )
    resource_id: Mapped[str | None] = mapped_column(String(32), index=True)
    resource_label: Mapped[str | None] = mapped_column(String(255))
    permission: Mapped[Permission | None] = mapped_column(EnumStr(Permission, 12))
    requested_role: Mapped[Role | None] = mapped_column(EnumStr(Role, 16))

    reason: Mapped[str | None] = mapped_column(Text)
    decided_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)

    requester: Mapped[User | None] = relationship(foreign_keys=[requester_id])
    decided_by: Mapped[User | None] = relationship(foreign_keys=[decided_by_id])

    __table_args__ = (
        Index("ix_access_requests_resource", "resource_type", "resource_id", "status"),
        UniqueConstraint(
            "requester_email",
            "type",
            "resource_type",
            "resource_id",
            "status",
            name="requester_resource_status",
        ),
    )


class RolePermissions(Base, TimestampMixin):
    __tablename__ = "role_permissions"

    role: Mapped[Role] = mapped_column(EnumStr(Role, 16), primary_key=True)
    permissions: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    label: Mapped[str | None] = mapped_column(String(120))
    updated_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Invite(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "invites"
    __id_prefix__ = "inv"

    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role: Mapped[Role] = mapped_column(EnumStr(Role, 16), nullable=False, default=Role.MEMBER)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    invited_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)

    invited_by: Mapped[User | None] = relationship(foreign_keys=[invited_by_id])
