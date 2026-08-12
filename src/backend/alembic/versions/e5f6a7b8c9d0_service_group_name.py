"""product_services.group_name

Nhóm dịch vụ chính bên trong một sản phẩm lớn — theo cấu trúc "Tính năng
chính" của docs.zaloplatforms.com. Ví dụ Zalo Business Solution chia 4 nhóm:
Official Account / Business Message / Menu & Chatbot / Analytics Dashboard;
"Tin giao dịch (Template giao dịch)" thuộc nhóm Business Message.

Chỉ phục vụ NHÓM HIỂN THỊ trong Product Knowledge Hub — đơn vị matching vẫn
là từng dịch vụ con.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product_services",
        sa.Column("group_name", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("product_services", "group_name")
