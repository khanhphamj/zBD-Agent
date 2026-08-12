"""product_services + customer_solutions.service_id

Tách đơn vị MATCHING khỏi đơn vị HIỂN THỊ: sản phẩm lớn (products) gồm nhiều
dịch vụ con (product_services); Product Matching chấm điểm theo dịch vụ con,
product giữ vai trò pitch + mang gói/giá. Nguồn dữ liệu dịch vụ con:
docs.zaloplatforms.com (seed riêng, xem seed_services.py).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "product_services",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("product_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        # solves/signals viết bằng ngôn ngữ painpoint — đầu vào chính của LLM
        sa.Column("solves", sa.Text(), nullable=True),
        sa.Column("signals", JSONB, nullable=False),
        sa.Column("capabilities", JSONB, nullable=False),
        # trỏ vào các dòng giá tương ứng của product cha (product vẫn là chủ giá)
        sa.Column("pricing_ref", JSONB, nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "slug", name="uq_product_services_product_slug"),
    )
    op.create_index("ix_product_services_product_id", "product_services", ["product_id"])
    op.create_index("ix_product_services_status", "product_services", ["status"])
    op.create_index("ix_product_services_created_at", "product_services", ["created_at"])
    op.create_index("ix_product_services_updated_at", "product_services", ["updated_at"])
    op.create_index(
        "ix_product_services_product_status", "product_services", ["product_id", "status"]
    )

    op.add_column(
        "customer_solutions", sa.Column("service_id", sa.String(length=32), nullable=True)
    )
    op.create_foreign_key(
        "fk_customer_solutions_service_id",
        "customer_solutions",
        "product_services",
        ["service_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_customer_solutions_service_id", "customer_solutions", ["service_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_solutions_service_id", table_name="customer_solutions")
    op.drop_constraint(
        "fk_customer_solutions_service_id", "customer_solutions", type_="foreignkey"
    )
    op.drop_column("customer_solutions", "service_id")
    op.drop_index("ix_product_services_product_status", table_name="product_services")
    op.drop_index("ix_product_services_updated_at", table_name="product_services")
    op.drop_index("ix_product_services_created_at", table_name="product_services")
    op.drop_index("ix_product_services_status", table_name="product_services")
    op.drop_index("ix_product_services_product_id", table_name="product_services")
    op.drop_table("product_services")
