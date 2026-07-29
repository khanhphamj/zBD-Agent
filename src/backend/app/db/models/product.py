from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

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
