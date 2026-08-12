"""products/product_services: requires + pricing_model

Mô hình hóa quan hệ phụ thuộc giữa sản phẩm/dịch vụ (vd: gửi ZBS Template
Message yêu cầu Zalo OA gói Tăng trưởng hoặc Toàn diện) và tách CÁCH TÍNH TIỀN
(per-use / subscription / included / unpublished) khỏi bảng giá — sửa tận gốc
lỗi dữ liệu kiểu "ZBS Template có gói Basic/Pro/Enterprise" (nguồn không hề có,
dịch vụ này tính theo đơn giá mỗi tin).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    for table in ("products", "product_services"):
        op.add_column(table, sa.Column("pricing_model", sa.String(length=24), nullable=True))
        op.add_column(
            table,
            sa.Column("requires", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        )


def downgrade() -> None:
    for table in ("products", "product_services"):
        op.drop_column(table, "requires")
        op.drop_column(table, "pricing_model")
