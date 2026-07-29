"""skill bundle column

Revision ID: 0669604fed00
Revises: c3750e3b8024
Create Date: 2026-07-29 14:53:46.187549

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0669604fed00"
down_revision: str | None = "c3750e3b8024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "skills",
        sa.Column(
            "bundle",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("skills", "bundle", server_default=None)


def downgrade() -> None:
    op.drop_column("skills", "bundle")
