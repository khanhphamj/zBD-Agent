from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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
from app.db.models.enums import Scope, SkillStage

if TYPE_CHECKING:
    from app.db.models.user import User


class Skill(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "skills"
    __id_prefix__ = "skl"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_norm: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    stage: Mapped[SkillStage] = mapped_column(EnumStr(SkillStage, 24), nullable=False, index=True)
    scope: Mapped[Scope] = mapped_column(
        EnumStr(Scope, 12), nullable=False, default=Scope.MINE, index=True
    )
    version: Mapped[str] = mapped_column(String(12), nullable=False, default="v1.0")
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")

    model: Mapped[str | None] = mapped_column(String(64))
    temperature: Mapped[float | None] = mapped_column(Float)

    input_schema: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, default=list, nullable=False
    )
    bundle: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    tools: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    owner_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    owner: Mapped["User | None"] = relationship(lazy="joined")
    versions: Mapped[list["SkillVersion"]] = relationship(
        back_populates="skill", cascade="all, delete-orphan"
    )


class SkillVersion(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "skill_versions"
    __id_prefix__ = "sver"

    skill_id: Mapped[str] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(12), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    author_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    skill: Mapped[Skill] = relationship(back_populates="versions")
    author: Mapped["User | None"] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("skill_id", "version", name="skill_version"),)


class SkillTestRun(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "skill_test_runs"
    __id_prefix__ = "strun"

    skill_id: Mapped[str] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[str | None] = mapped_column(String(12))
    input: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    output: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(String(64))
    run_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    skill: Mapped[Skill] = relationship()


class ProposalTemplate(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "proposal_templates"
    __id_prefix__ = "tpl"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_norm: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subtitle: Mapped[str | None] = mapped_column(String(512))
    scope: Mapped[Scope] = mapped_column(
        EnumStr(Scope, 12), nullable=False, default=Scope.MINE, index=True
    )
    version: Mapped[str] = mapped_column(String(12), nullable=False, default="v1.0")
    slide_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    required_slides: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    tone: Mapped[str | None] = mapped_column(String(32))
    cover_url: Mapped[str | None] = mapped_column(String(512))
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    owner_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    object_key: Mapped[str | None] = mapped_column(String(512))
    source_filename: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    preview_key: Mapped[str | None] = mapped_column(String(512))

    theme: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    owner: Mapped["User | None"] = relationship(lazy="joined")
    slides: Mapped[list["TemplateSlide"]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="TemplateSlide.index",
    )
    versions: Mapped[list["TemplateVersion"]] = relationship(
        back_populates="template", cascade="all, delete-orphan"
    )


class TemplateSlide(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "template_slides"
    __id_prefix__ = "tsl"

    template_id: Mapped[str] = mapped_column(
        ForeignKey("proposal_templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    layout: Mapped[str] = mapped_column(String(48), nullable=False, default="content")
    image_url: Mapped[str | None] = mapped_column(String(512))

    placeholders: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, default=list, nullable=False
    )
    guidance: Mapped[str | None] = mapped_column(Text)
    optional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    template: Mapped[ProposalTemplate] = relationship(back_populates="slides")

    __table_args__ = (
        UniqueConstraint("template_id", "index", name="template_slide_index"),
        Index("ix_template_slides_order", "template_id", "index"),
    )


class TemplateVersion(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "template_versions"
    __id_prefix__ = "tver"

    template_id: Mapped[str] = mapped_column(
        ForeignKey("proposal_templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(12), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    author_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    template: Mapped[ProposalTemplate] = relationship(back_populates="versions")
    author: Mapped["User | None"] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("template_id", "version", name="template_version"),)
