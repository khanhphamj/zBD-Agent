from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONType, PublicIdMixin, TimestampMixin


class Product(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "products"
    __id_prefix__ = "prd"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_norm: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    family: Mapped[str | None] = mapped_column(String(180), index=True)

    group_name: Mapped[str | None] = mapped_column("group_name", String(120), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    icon_url: Mapped[str | None] = mapped_column(String(512))

    packages: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)

    # Cách tính tiền: per-use / subscription / included / unpublished — tách
    # khỏi bảng giá để không nhét nhầm "gói" vào sản phẩm tính theo đơn giá.
    pricing_model: Mapped[str | None] = mapped_column(String(24))
    # Điều kiện tiên quyết: [{kind: package|product|account, ref, packages[],
    # optional, note, source}] — vd ZBS Template Message cần OA gói
    # Tăng trưởng/Toàn diện rồi mới tính đơn giá mỗi tin.
    requires: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, default=list, nullable=False
    )

    positioning: Mapped[str | None] = mapped_column(Text)
    use_cases: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list, nullable=False)
    pricing: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list, nullable=False)
    documents: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list, nullable=False)

    capabilities: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    target_industries: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)

    source_url: Mapped[str | None] = mapped_column(String(1024))
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (Index("ix_products_family_group", "family", "group_name", "status"),)


class ProductService(Base, PublicIdMixin, TimestampMixin):
    """Dịch vụ con bên trong một sản phẩm lớn — ĐƠN VỊ MATCHING.

    Product Matching chấm điểm theo dịch vụ con (painpoint khớp năng lực cụ
    thể), còn Product là đơn vị hiển thị/pitch và là chủ của gói + giá.
    `solves` và `signals` viết bằng ngôn ngữ painpoint để LLM đối chiếu thẳng
    với dữ kiện Deep Research. Nguồn dữ liệu: docs.zaloplatforms.com.
    """

    __tablename__ = "product_services"
    __id_prefix__ = "svc"

    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    solves: Mapped[str | None] = mapped_column(Text)
    signals: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    # Trỏ vào các dòng giá tương ứng của product cha; product vẫn là chủ giá.
    pricing_ref: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, default=list, nullable=False
    )
    pricing_model: Mapped[str | None] = mapped_column(String(24))
    # Điều kiện riêng của dịch vụ (khác điều kiện của product cha thì mới ghi).
    requires: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, default=list, nullable=False
    )
    # Nhóm dịch vụ chính bên trong sản phẩm (ZBS: Official Account / Business
    # Message / Menu & Chatbot / Analytics Dashboard) — chỉ để NHÓM HIỂN THỊ
    # trong PKH, đơn vị matching vẫn là từng dịch vụ.
    group_name: Mapped[str | None] = mapped_column(String(120))
    source_url: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    product: Mapped[Product] = relationship(backref="services")

    __table_args__ = (
        Index("ix_product_services_product_status", "product_id", "status"),
        UniqueConstraint("product_id", "slug", name="uq_product_services_product_slug"),
    )
