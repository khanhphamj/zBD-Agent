from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, EnumStr, TimestampMixin
from app.db.models.enums import ResearchDepth

if TYPE_CHECKING:
    from app.db.models.user import User


class AiConfig(Base, TimestampMixin):
    __tablename__ = "ai_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    model: Mapped[str] = mapped_column(String(64), nullable=False, default="z-ai/glm-5.2")
    vision_model: Mapped[str] = mapped_column(
        String(64), nullable=False, default="google/gemma-4-31b-it"
    )
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.4)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=8000)
    research_depth: Mapped[ResearchDepth] = mapped_column(
        EnumStr(ResearchDepth, 16), nullable=False, default=ResearchDepth.STANDARD
    )

    auto_approve_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=85)
    monthly_budget_usd: Mapped[float] = mapped_column(Float, nullable=False, default=2000)

    month_spend_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    updated_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped["User | None"] = relationship()

    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)


class SystemPolicy(Base, TimestampMixin):
    __tablename__ = "system_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    allow_company_link: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    require_admin_approval_publish: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    block_external_share: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    updated_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped["User | None"] = relationship()

    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)
