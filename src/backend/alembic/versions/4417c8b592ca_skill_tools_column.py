"""skill tools column

Revision ID: 4417c8b592ca
Revises: 0669604fed00
Create Date: 2026-07-29 16:37:14.028962

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "4417c8b592ca"
down_revision: str | None = "0669604fed00"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "skills",
        sa.Column(
            "tools",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("skills", "tools", server_default=None)


def downgrade() -> None:
    op.drop_column("skills", "tools")
