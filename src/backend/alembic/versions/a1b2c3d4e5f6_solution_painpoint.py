"""customer_solutions: painpoint + opportunity

Product Matching đi theo chuỗi dữ kiện -> painpoint/cơ hội -> sản phẩm, nên hai
bước đầu cần lưu lại thay vì chỉ còn rationale.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "7b4b7653be80"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("customer_solutions", sa.Column("painpoint", sa.Text(), nullable=True))
    op.add_column("customer_solutions", sa.Column("opportunity", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("customer_solutions", "opportunity")
    op.drop_column("customer_solutions", "painpoint")
