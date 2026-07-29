from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
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

from app.db.base import Base, EnumStr, JSONType, PublicIdMixin, TimestampMixin
from app.db.models.enums import FileStatus, Scope

if TYPE_CHECKING:
    from app.db.models.user import User


class KnowledgeBase(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "knowledge_bases"
    __id_prefix__ = "kb"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_norm: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[Scope] = mapped_column(
        EnumStr(Scope, 12), nullable=False, default=Scope.MINE, index=True
    )
    owner_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    tags: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    restricted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    doc_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    icon: Mapped[str | None] = mapped_column(String(64))

    owner: Mapped["User | None"] = relationship(lazy="joined")
    files: Mapped[list["KnowledgeFile"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )
    sources: Mapped[list["KnowledgeSource"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )


class KnowledgeFile(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "knowledge_files"
    __id_prefix__ = "fil"

    kb_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ext: Mapped[str] = mapped_column(String(8), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)

    status: Mapped[FileStatus] = mapped_column(
        EnumStr(FileStatus, 16), nullable=False, default=FileStatus.UPLOADED, index=True
    )
    index_error: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)

    extracted_text: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    uploaded_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="files")
    uploaded_by: Mapped["User | None"] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("kb_id", "content_hash", name="kb_content_hash"),
        Index("ix_kb_files_status", "kb_id", "status"),
    )


class KnowledgeSource(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "knowledge_sources"
    __id_prefix__ = "kbs"

    kb_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    doc_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="sources")

    __table_args__ = (UniqueConstraint("kb_id", "url", name="kb_source_url"),)
