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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, EnumStr, JSONType, PublicIdMixin, TimestampMixin
from app.db.models.enums import (
    ActivityKind,
    CustomerStatus,
    FactVerdict,
    Permission,
    SourceStatus,
)

if TYPE_CHECKING:
    from app.db.models.product import Product, ProductService
    from app.db.models.user import User


class Customer(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "customers"
    __id_prefix__ = "cus"

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    name_norm: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    short_name: Mapped[str | None] = mapped_column(String(32))
    website: Mapped[str | None] = mapped_column(String(255))
    website_norm: Mapped[str | None] = mapped_column(String(255), index=True)
    industry: Mapped[str | None] = mapped_column(String(120), index=True)

    status: Mapped[CustomerStatus] = mapped_column(
        EnumStr(CustomerStatus, 48), nullable=False, default=CustomerStatus.NEW, index=True
    )
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    logo_url: Mapped[str | None] = mapped_column(String(512))
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    profile: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fact_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    opportunity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    researched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_research_job_id: Mapped[str | None] = mapped_column(String(32))

    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    owner: Mapped["User"] = relationship(
        back_populates="owned_customers", foreign_keys=[owner_id], lazy="joined"
    )
    facts: Mapped[list["CustomerFact"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    sources: Mapped[list["CustomerSource"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    activities: Mapped[list["CustomerActivity"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    solutions: Mapped[list["CustomerSolution"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_customers_owner_status", "owner_id", "status", "archived"),
        Index("ix_customers_updated", "archived", "updated_at"),
    )


class CustomerFact(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "customer_facts"
    __id_prefix__ = "f"

    customer_id: Mapped[str] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    topic: Mapped[str] = mapped_column(String(180), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verdict: Mapped[FactVerdict] = mapped_column(
        EnumStr(FactVerdict, 12), nullable=False, default=FactVerdict.AI, index=True
    )
    source_url: Mapped[str | None] = mapped_column(String(1024))

    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="research")
    job_id: Mapped[str | None] = mapped_column(String(32), index=True)
    is_opportunity: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    verified_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship(back_populates="facts")
    verified_by: Mapped["User | None"] = relationship(lazy="joined")


class CustomerSource(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "customer_sources"
    __id_prefix__ = "src"

    customer_id: Mapped[str] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str | None] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(180))
    url: Mapped[str | None] = mapped_column(String(1024))
    title: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[SourceStatus] = mapped_column(
        EnumStr(SourceStatus, 12), nullable=False, default=SourceStatus.PENDING
    )
    http_status: Mapped[int | None] = mapped_column(Integer)

    reason: Mapped[str | None] = mapped_column(String(64))
    excerpt: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship(back_populates="sources")


class CustomerActivity(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "customer_activities"
    __id_prefix__ = "act"

    customer_id: Mapped[str] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[ActivityKind] = mapped_column(
        EnumStr(ActivityKind, 32), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="activities")
    actor: Mapped["User | None"] = relationship(lazy="joined")

    __table_args__ = (Index("ix_activity_customer_created", "customer_id", "created_at"),)


class CustomerHandover(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "customer_handovers"
    __id_prefix__ = "hov"

    customer_id: Mapped[str] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    to_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    keep_permission: Mapped[Permission] = mapped_column(
        EnumStr(Permission, 12), nullable=False, default=Permission.VIEW
    )
    note: Mapped[str | None] = mapped_column(Text)

    customer: Mapped[Customer] = relationship()
    from_user: Mapped["User"] = relationship(foreign_keys=[from_user_id])
    to_user: Mapped["User"] = relationship(foreign_keys=[to_user_id])


class CustomerSolution(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "customer_solutions"
    __id_prefix__ = "sol"

    customer_id: Mapped[str] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), index=True
    )
    # Dịch vụ con trúng match; product_id luôn được server suy từ service nên
    # cặp này không bao giờ lệch nhau. NULL = match ở mức sản phẩm (fallback).
    service_id: Mapped[str | None] = mapped_column(
        ForeignKey("product_services.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # Chuỗi suy luận của Product Matching: painpoint đọc ra từ dữ kiện Deep
    # Research -> cơ hội khai thác -> rationale nối sang sản phẩm.
    painpoint: Mapped[str | None] = mapped_column(Text)
    opportunity: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)

    fit_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    expected_impact: Mapped[str | None] = mapped_column(Text)
    pricing_note: Mapped[str | None] = mapped_column(Text)
    package: Mapped[str | None] = mapped_column(String(64))

    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job_id: Mapped[str | None] = mapped_column(String(32), index=True)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list, nullable=False)
    # Chi tiết từng dịch vụ khớp trong CÙNG sản phẩm (một phương án = một sản
    # phẩm): [{serviceId, serviceName, fitScore, painpoint, opportunity,
    # rationale, expectedImpact, package, pricingNote, evidence[]}].
    # Các cột đơn (painpoint, package...) giữ giá trị của dịch vụ điểm cao nhất
    # để UI card cũ vẫn hiển thị được.
    service_matches: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, default=list, nullable=False
    )

    customer: Mapped[Customer] = relationship(back_populates="solutions")
    product: Mapped["Product | None"] = relationship(lazy="joined")
    service: Mapped["ProductService | None"] = relationship(lazy="joined")
