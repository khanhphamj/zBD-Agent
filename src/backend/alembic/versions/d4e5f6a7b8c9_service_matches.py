"""customer_solutions.service_matches

Một phương án matching = MỘT SẢN PHẨM; chi tiết từng dịch vụ khớp (painpoint,
điểm, gói, evidence riêng) nằm trong service_matches thay vì nhân bản phương án
theo dịch vụ (trước đây Mini App Platform xuất hiện 2 dòng cho 2 dịch vụ).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column(
        "customer_solutions",
        sa.Column("service_matches", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("customer_solutions", "service_matches")
