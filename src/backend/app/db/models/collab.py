from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, EnumStr, JSONType, PublicIdMixin, TimestampMixin
from app.db.models.enums import (
    AuditCategory,
    LinkMode,
    NotificationType,
    Permission,
    ResourceType,
)

if TYPE_CHECKING:
    from app.db.models.user import Team, User


class Share(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "shares"
    __id_prefix__ = "shr"

    resource_type: Mapped[ResourceType] = mapped_column(EnumStr(ResourceType, 24), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(32), nullable=False)

    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    team_id: Mapped[str | None] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    permission: Mapped[Permission] = mapped_column(
        EnumStr(Permission, 12), nullable=False, default=Permission.VIEW
    )
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    user: Mapped["User | None"] = relationship(foreign_keys=[user_id], lazy="joined")
    team: Mapped["Team | None"] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_shares_resource", "resource_type", "resource_id"),
        UniqueConstraint("resource_type", "resource_id", "user_id", name="share_user"),
        UniqueConstraint("resource_type", "resource_id", "team_id", name="share_team"),
    )


class ShareLink(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "share_links"
    __id_prefix__ = "lnk"

    resource_type: Mapped[ResourceType] = mapped_column(EnumStr(ResourceType, 24), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[LinkMode] = mapped_column(
        EnumStr(LinkMode, 16), nullable=False, default=LinkMode.RESTRICTED
    )
    permission: Mapped[Permission] = mapped_column(
        EnumStr(Permission, 12), nullable=False, default=Permission.VIEW
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    updated_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    __table_args__ = (UniqueConstraint("resource_type", "resource_id", name="share_link_resource"),)


class Notification(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "notifications"
    __id_prefix__ = "ntf"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[NotificationType] = mapped_column(
        EnumStr(NotificationType, 24), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)

    target_url: Mapped[str | None] = mapped_column(String(512))
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    actor: Mapped["User | None"] = relationship(foreign_keys=[actor_id], lazy="joined")

    __table_args__ = (Index("ix_notifications_user_read", "user_id", "read", "created_at"),)


class AuditLog(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "audit_logs"
    __id_prefix__ = "aud"

    actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    actor_label: Mapped[str | None] = mapped_column(String(180))
    category: Mapped[AuditCategory] = mapped_column(
        EnumStr(AuditCategory, 24), nullable=False, index=True
    )

    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(32), index=True)
    resource_id: Mapped[str | None] = mapped_column(String(32), index=True)
    resource_label: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(String(512), nullable=False)

    search_norm: Mapped[str | None] = mapped_column(Text, index=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))

    actor: Mapped["User | None"] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_audit_category_created", "category", "created_at"),
        Index("ix_audit_actor_created", "actor_id", "created_at"),
    )
