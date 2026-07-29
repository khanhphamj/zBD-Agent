from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, EnumStr, JSONType, PublicIdMixin, TimestampMixin
from app.db.models.enums import (
    ExportFormat,
    ExportStage,
    ExportStatus,
    ProposalStatus,
    RequestStatus,
)

if TYPE_CHECKING:
    from app.db.models.customer import Customer
    from app.db.models.library import ProposalTemplate
    from app.db.models.user import User


class Proposal(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "proposals"
    __id_prefix__ = "prp"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    title_norm: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    template_id: Mapped[str | None] = mapped_column(
        ForeignKey("proposal_templates.id", ondelete="SET NULL"), index=True
    )
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    version: Mapped[str] = mapped_column(String(12), nullable=False, default="v1.0")
    status: Mapped[ProposalStatus] = mapped_column(
        EnumStr(ProposalStatus, 24), nullable=False, default=ProposalStatus.DRAFT, index=True
    )
    slide_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    tone: Mapped[str | None] = mapped_column(String(32))

    outline: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list, nullable=False)

    generation_params: Mapped[dict[str, Any]] = mapped_column(
        JSONType, default=dict, nullable=False
    )
    summary: Mapped[str | None] = mapped_column(Text)
    last_generate_job_id: Mapped[str | None] = mapped_column(String(32))

    customer: Mapped["Customer"] = relationship(lazy="joined")
    template: Mapped["ProposalTemplate | None"] = relationship(lazy="joined")
    owner: Mapped["User"] = relationship(lazy="joined")
    slides: Mapped[list["ProposalSlide"]] = relationship(
        back_populates="proposal",
        cascade="all, delete-orphan",
        order_by="ProposalSlide.index",
    )
    versions: Mapped[list["ProposalVersion"]] = relationship(
        back_populates="proposal", cascade="all, delete-orphan"
    )
    comments: Mapped[list["ProposalComment"]] = relationship(
        back_populates="proposal", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_proposals_owner_status", "owner_id", "status", "archived"),
        Index("ix_proposals_updated", "archived", "updated_at"),
    )


class ProposalSlide(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "proposal_slides"
    __id_prefix__ = "s"

    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False, index=True
    )

    index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    layout: Mapped[str] = mapped_column(String(48), nullable=False, default="content")

    blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list, nullable=False)
    speaker_notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="done")

    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list, nullable=False)

    proposal: Mapped[Proposal] = relationship(back_populates="slides")

    __table_args__ = (
        UniqueConstraint("proposal_id", "index", name="slide_index"),
        Index("ix_slides_proposal_index", "proposal_id", "index"),
    )


class ProposalVersion(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "proposal_versions"
    __id_prefix__ = "pver"

    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(12), nullable=False)
    author_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)

    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)

    proposal: Mapped[Proposal] = relationship(back_populates="versions")
    author: Mapped["User | None"] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("proposal_id", "version", name="proposal_version"),)


class ProposalComment(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "proposal_comments"
    __id_prefix__ = "cmt"

    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slide_id: Mapped[str | None] = mapped_column(
        ForeignKey("proposal_slides.id", ondelete="CASCADE"), index=True
    )

    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("proposal_comments.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    anchor: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    resolved_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    proposal: Mapped[Proposal] = relationship(back_populates="comments")
    author: Mapped["User | None"] = relationship(foreign_keys=[author_id], lazy="joined")
    replies: Mapped[list["ProposalComment"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    parent: Mapped["ProposalComment | None"] = relationship(
        back_populates="replies", remote_side="ProposalComment.id"
    )


class ProposalApproval(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "proposal_approvals"
    __id_prefix__ = "apr"

    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    approver_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[RequestStatus] = mapped_column(
        EnumStr(RequestStatus, 16), nullable=False, default=RequestStatus.PENDING, index=True
    )

    round: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    note: Mapped[str | None] = mapped_column(Text)

    reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[str | None] = mapped_column(String(12))

    proposal: Mapped[Proposal] = relationship()
    approver: Mapped["User"] = relationship(foreign_keys=[approver_id], lazy="joined")
    requested_by: Mapped["User | None"] = relationship(foreign_keys=[requested_by_id])


class ProposalExport(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "proposal_exports"
    __id_prefix__ = "exp"

    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    format: Mapped[ExportFormat] = mapped_column(EnumStr(ExportFormat, 8), nullable=False)
    template_id: Mapped[str | None] = mapped_column(String(32))
    include_appendix: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    status: Mapped[ExportStatus] = mapped_column(
        EnumStr(ExportStatus, 16), nullable=False, default=ExportStatus.PROCESSING, index=True
    )
    stage: Mapped[ExportStage] = mapped_column(
        EnumStr(ExportStage, 16), nullable=False, default=ExportStage.PREPARE
    )
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    object_key: Mapped[str | None] = mapped_column(String(512))
    filename: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(Integer)

    error: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    proposal: Mapped[Proposal] = relationship()
    created_by: Mapped["User | None"] = relationship(lazy="joined")


class AiChatMessage(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "ai_chat_messages"
    __id_prefix__ = "msg"

    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slide_id: Mapped[str | None] = mapped_column(String(32))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    context: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(String(64))

    proposal: Mapped[Proposal] = relationship()

    __table_args__ = (Index("ix_chat_proposal_created", "proposal_id", "created_at"),)


class AiSuggestedChange(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "ai_suggested_changes"
    __id_prefix__ = "chg"

    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_chat_messages.id", ondelete="CASCADE"), index=True
    )
    slide_id: Mapped[str | None] = mapped_column(String(32))

    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)

    patch: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    proposal: Mapped[Proposal] = relationship()
