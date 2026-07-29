from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c3750e3b8024"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION zbd_unaccent(text)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        STRICT
        AS $$
          SELECT public.unaccent('public.unaccent'::regdictionary, lower($1))
        $$
        """
    )

    op.create_table(
        "otp_challenges",
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("send_count", sa.Integer(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_otp_challenges")),
    )
    op.create_index(
        op.f("ix_otp_challenges_created_at"), "otp_challenges", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_otp_challenges_email"), "otp_challenges", ["email"], unique=False)
    op.create_index(
        op.f("ix_otp_challenges_updated_at"), "otp_challenges", ["updated_at"], unique=False
    )
    op.create_index("ix_otp_email_created", "otp_challenges", ["email", "created_at"], unique=False)
    op.create_table(
        "products",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("name_norm", sa.String(length=255), nullable=False),
        sa.Column("family", sa.String(length=180), nullable=True),
        sa.Column("group_name", sa.String(length=120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("icon_url", sa.String(length=512), nullable=True),
        sa.Column(
            "packages",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("positioning", sa.Text(), nullable=True),
        sa.Column(
            "use_cases",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "pricing",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "documents",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "capabilities",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "target_industries",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("last_crawled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
    )
    op.create_index(op.f("ix_products_created_at"), "products", ["created_at"], unique=False)
    op.create_index(op.f("ix_products_family"), "products", ["family"], unique=False)
    op.create_index(
        "ix_products_family_group", "products", ["family", "group_name", "status"], unique=False
    )
    op.create_index(op.f("ix_products_group_name"), "products", ["group_name"], unique=False)
    op.create_index(op.f("ix_products_name_norm"), "products", ["name_norm"], unique=False)
    op.create_index(op.f("ix_products_status"), "products", ["status"], unique=False)
    op.create_index(op.f("ix_products_updated_at"), "products", ["updated_at"], unique=False)
    op.create_table(
        "teams",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("name_norm", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_teams")),
        sa.UniqueConstraint("name", name=op.f("uq_teams_name")),
    )
    op.create_index(op.f("ix_teams_created_at"), "teams", ["created_at"], unique=False)
    op.create_index(op.f("ix_teams_name_norm"), "teams", ["name_norm"], unique=False)
    op.create_index(op.f("ix_teams_updated_at"), "teams", ["updated_at"], unique=False)
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("handle", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("name_norm", sa.String(length=180), nullable=False),
        sa.Column("initials", sa.String(length=4), nullable=False),
        sa.Column("avatar_color", sa.String(length=9), nullable=False),
        sa.Column("avatar_url", sa.String(length=512), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("team_id", sa.String(length=32), nullable=True),
        sa.Column("title", sa.String(length=180), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("is_first_login", sa.Boolean(), nullable=False),
        sa.Column(
            "onboarding_steps",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("tour_completed", sa.Boolean(), nullable=False),
        sa.Column(
            "interests",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by_id", sa.String(length=32), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["invited_by_id"],
            ["users.id"],
            name=op.f("fk_users_invited_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f("fk_users_team_id_teams"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_created_at"), "users", ["created_at"], unique=False)
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_handle"), "users", ["handle"], unique=False)
    op.create_index(op.f("ix_users_name_norm"), "users", ["name_norm"], unique=False)
    op.create_index(op.f("ix_users_role"), "users", ["role"], unique=False)
    op.create_index(op.f("ix_users_status"), "users", ["status"], unique=False)
    op.create_index(op.f("ix_users_team_id"), "users", ["team_id"], unique=False)
    op.create_index(op.f("ix_users_updated_at"), "users", ["updated_at"], unique=False)
    op.create_table(
        "access_requests",
        sa.Column("type", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requester_id", sa.String(length=32), nullable=True),
        sa.Column("requester_email", sa.String(length=255), nullable=False),
        sa.Column("requester_name", sa.String(length=180), nullable=True),
        sa.Column("resource_type", sa.String(length=24), nullable=True),
        sa.Column("resource_id", sa.String(length=32), nullable=True),
        sa.Column("resource_label", sa.String(length=255), nullable=True),
        sa.Column("permission", sa.String(length=12), nullable=True),
        sa.Column("requested_role", sa.String(length=16), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decided_by_id", sa.String(length=32), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["decided_by_id"],
            ["users.id"],
            name=op.f("fk_access_requests_decided_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requester_id"],
            ["users.id"],
            name=op.f("fk_access_requests_requester_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_access_requests")),
        sa.UniqueConstraint(
            "requester_email",
            "type",
            "resource_type",
            "resource_id",
            "status",
            name="requester_resource_status",
        ),
    )
    op.create_index(
        op.f("ix_access_requests_created_at"), "access_requests", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_access_requests_requester_email"),
        "access_requests",
        ["requester_email"],
        unique=False,
    )
    op.create_index(
        op.f("ix_access_requests_requester_id"), "access_requests", ["requester_id"], unique=False
    )
    op.create_index(
        "ix_access_requests_resource",
        "access_requests",
        ["resource_type", "resource_id", "status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_access_requests_resource_id"), "access_requests", ["resource_id"], unique=False
    )
    op.create_index(
        op.f("ix_access_requests_resource_type"), "access_requests", ["resource_type"], unique=False
    )
    op.create_index(op.f("ix_access_requests_status"), "access_requests", ["status"], unique=False)
    op.create_index(op.f("ix_access_requests_type"), "access_requests", ["type"], unique=False)
    op.create_index(
        op.f("ix_access_requests_updated_at"), "access_requests", ["updated_at"], unique=False
    )
    op.create_table(
        "ai_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("vision_model", sa.String(length=64), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("research_depth", sa.String(length=16), nullable=False),
        sa.Column("auto_approve_threshold", sa.Integer(), nullable=False),
        sa.Column("monthly_budget_usd", sa.Float(), nullable=False),
        sa.Column("month_spend_usd", sa.Float(), nullable=False),
        sa.Column("updated_by_id", sa.String(length=32), nullable=True),
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
        sa.CheckConstraint("id = 1", name=op.f("ck_ai_config_singleton")),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_ai_config_updated_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_config")),
    )
    op.create_index(op.f("ix_ai_config_created_at"), "ai_config", ["created_at"], unique=False)
    op.create_index(op.f("ix_ai_config_updated_at"), "ai_config", ["updated_at"], unique=False)
    op.create_table(
        "audit_logs",
        sa.Column("actor_id", sa.String(length=32), nullable=True),
        sa.Column("actor_label", sa.String(length=180), nullable=True),
        sa.Column("category", sa.String(length=24), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=True),
        sa.Column("resource_id", sa.String(length=32), nullable=True),
        sa.Column("resource_label", sa.String(length=255), nullable=True),
        sa.Column("summary", sa.String(length=512), nullable=False),
        sa.Column("search_norm", sa.Text(), nullable=True),
        sa.Column(
            "meta",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_audit_logs_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        "ix_audit_actor_created", "audit_logs", ["actor_id", "created_at"], unique=False
    )
    op.create_index(
        "ix_audit_category_created", "audit_logs", ["category", "created_at"], unique=False
    )
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False)
    op.create_index(op.f("ix_audit_logs_actor_id"), "audit_logs", ["actor_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_category"), "audit_logs", ["category"], unique=False)
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"], unique=False)
    op.create_index(op.f("ix_audit_logs_resource_id"), "audit_logs", ["resource_id"], unique=False)
    op.create_index(
        op.f("ix_audit_logs_resource_type"), "audit_logs", ["resource_type"], unique=False
    )
    op.create_index(op.f("ix_audit_logs_search_norm"), "audit_logs", ["search_norm"], unique=False)
    op.create_index(op.f("ix_audit_logs_updated_at"), "audit_logs", ["updated_at"], unique=False)
    op.create_table(
        "customers",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("name_norm", sa.String(length=255), nullable=False),
        sa.Column("short_name", sa.String(length=32), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("website_norm", sa.String(length=255), nullable=True),
        sa.Column("industry", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=48), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=False),
        sa.Column("logo_url", sa.String(length=512), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column(
            "profile",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("fact_count", sa.Integer(), nullable=False),
        sa.Column("opportunity_count", sa.Integer(), nullable=False),
        sa.Column("researched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_research_job_id", sa.String(length=32), nullable=True),
        sa.Column("created_by_id", sa.String(length=32), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_customers_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_customers_owner_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customers")),
    )
    op.create_index(op.f("ix_customers_archived"), "customers", ["archived"], unique=False)
    op.create_index(op.f("ix_customers_created_at"), "customers", ["created_at"], unique=False)
    op.create_index(op.f("ix_customers_industry"), "customers", ["industry"], unique=False)
    op.create_index(op.f("ix_customers_name_norm"), "customers", ["name_norm"], unique=False)
    op.create_index(op.f("ix_customers_owner_id"), "customers", ["owner_id"], unique=False)
    op.create_index(
        "ix_customers_owner_status", "customers", ["owner_id", "status", "archived"], unique=False
    )
    op.create_index(op.f("ix_customers_status"), "customers", ["status"], unique=False)
    op.create_index("ix_customers_updated", "customers", ["archived", "updated_at"], unique=False)
    op.create_index(op.f("ix_customers_updated_at"), "customers", ["updated_at"], unique=False)
    op.create_index(op.f("ix_customers_website_norm"), "customers", ["website_norm"], unique=False)
    op.create_table(
        "invites",
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("team_id", sa.String(length=32), nullable=True),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("invited_by_id", sa.String(length=32), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "meta",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["invited_by_id"],
            ["users.id"],
            name=op.f("fk_invites_invited_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f("fk_invites_team_id_teams"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invites")),
    )
    op.create_index(op.f("ix_invites_created_at"), "invites", ["created_at"], unique=False)
    op.create_index(op.f("ix_invites_email"), "invites", ["email"], unique=False)
    op.create_index(op.f("ix_invites_token"), "invites", ["token"], unique=True)
    op.create_index(op.f("ix_invites_updated_at"), "invites", ["updated_at"], unique=False)
    op.create_table(
        "knowledge_bases",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("name_norm", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scope", sa.String(length=12), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=True),
        sa.Column(
            "tags",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("restricted", sa.Boolean(), nullable=False),
        sa.Column("doc_count", sa.Integer(), nullable=False),
        sa.Column("icon", sa.String(length=64), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_knowledge_bases_owner_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_bases")),
    )
    op.create_index(
        op.f("ix_knowledge_bases_created_at"), "knowledge_bases", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_knowledge_bases_name_norm"), "knowledge_bases", ["name_norm"], unique=False
    )
    op.create_index(
        op.f("ix_knowledge_bases_owner_id"), "knowledge_bases", ["owner_id"], unique=False
    )
    op.create_index(op.f("ix_knowledge_bases_scope"), "knowledge_bases", ["scope"], unique=False)
    op.create_index(
        op.f("ix_knowledge_bases_updated_at"), "knowledge_bases", ["updated_at"], unique=False
    )
    op.create_table(
        "notifications",
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("type", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("target_url", sa.String(length=512), nullable=True),
        sa.Column("read", sa.Boolean(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor_id", sa.String(length=32), nullable=True),
        sa.Column(
            "meta",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_notifications_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notifications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index(
        op.f("ix_notifications_created_at"), "notifications", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_notifications_read"), "notifications", ["read"], unique=False)
    op.create_index(op.f("ix_notifications_type"), "notifications", ["type"], unique=False)
    op.create_index(
        op.f("ix_notifications_updated_at"), "notifications", ["updated_at"], unique=False
    )
    op.create_index(op.f("ix_notifications_user_id"), "notifications", ["user_id"], unique=False)
    op.create_index(
        "ix_notifications_user_read",
        "notifications",
        ["user_id", "read", "created_at"],
        unique=False,
    )
    op.create_table(
        "proposal_templates",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("name_norm", sa.String(length=255), nullable=False),
        sa.Column("subtitle", sa.String(length=512), nullable=True),
        sa.Column("scope", sa.String(length=12), nullable=False),
        sa.Column("version", sa.String(length=12), nullable=False),
        sa.Column("slide_count", sa.Integer(), nullable=False),
        sa.Column(
            "required_slides",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("tone", sa.String(length=32), nullable=True),
        sa.Column("cover_url", sa.String(length=512), nullable=True),
        sa.Column("used_count", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=True),
        sa.Column("object_key", sa.String(length=512), nullable=True),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "theme",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_proposal_templates_owner_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposal_templates")),
    )
    op.create_index(
        op.f("ix_proposal_templates_created_at"), "proposal_templates", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_templates_name_norm"), "proposal_templates", ["name_norm"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_templates_owner_id"), "proposal_templates", ["owner_id"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_templates_scope"), "proposal_templates", ["scope"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_templates_updated_at"), "proposal_templates", ["updated_at"], unique=False
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
    )
    op.create_index(
        op.f("ix_refresh_tokens_created_at"), "refresh_tokens", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_refresh_tokens_token_hash"), "refresh_tokens", ["token_hash"], unique=True
    )
    op.create_index(
        op.f("ix_refresh_tokens_updated_at"), "refresh_tokens", ["updated_at"], unique=False
    )
    op.create_index(op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"], unique=False)
    op.create_table(
        "role_permissions",
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "permissions",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("updated_by_id", sa.String(length=32), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_role_permissions_updated_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("role", name=op.f("pk_role_permissions")),
    )
    op.create_index(
        op.f("ix_role_permissions_created_at"), "role_permissions", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_role_permissions_updated_at"), "role_permissions", ["updated_at"], unique=False
    )
    op.create_table(
        "share_links",
        sa.Column("resource_type", sa.String(length=24), nullable=False),
        sa.Column("resource_id", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("permission", sa.String(length=12), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("updated_by_id", sa.String(length=32), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_share_links_updated_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_share_links")),
        sa.UniqueConstraint("resource_type", "resource_id", name="share_link_resource"),
    )
    op.create_index(op.f("ix_share_links_created_at"), "share_links", ["created_at"], unique=False)
    op.create_index(op.f("ix_share_links_token"), "share_links", ["token"], unique=True)
    op.create_index(op.f("ix_share_links_updated_at"), "share_links", ["updated_at"], unique=False)
    op.create_table(
        "shares",
        sa.Column("resource_type", sa.String(length=24), nullable=False),
        sa.Column("resource_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=True),
        sa.Column("team_id", sa.String(length=32), nullable=True),
        sa.Column("permission", sa.String(length=12), nullable=False),
        sa.Column("created_by_id", sa.String(length=32), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_shares_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f("fk_shares_team_id_teams"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_shares_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shares")),
        sa.UniqueConstraint("resource_type", "resource_id", "team_id", name="share_team"),
        sa.UniqueConstraint("resource_type", "resource_id", "user_id", name="share_user"),
    )
    op.create_index(op.f("ix_shares_created_at"), "shares", ["created_at"], unique=False)
    op.create_index("ix_shares_resource", "shares", ["resource_type", "resource_id"], unique=False)
    op.create_index(op.f("ix_shares_team_id"), "shares", ["team_id"], unique=False)
    op.create_index(op.f("ix_shares_updated_at"), "shares", ["updated_at"], unique=False)
    op.create_index(op.f("ix_shares_user_id"), "shares", ["user_id"], unique=False)
    op.create_table(
        "skills",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("name_norm", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("scope", sa.String(length=12), nullable=False),
        sa.Column("version", sa.String(length=12), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column(
            "input_schema",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.String(length=32), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_skills_owner_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skills")),
    )
    op.create_index(op.f("ix_skills_created_at"), "skills", ["created_at"], unique=False)
    op.create_index(op.f("ix_skills_name_norm"), "skills", ["name_norm"], unique=False)
    op.create_index(op.f("ix_skills_owner_id"), "skills", ["owner_id"], unique=False)
    op.create_index(op.f("ix_skills_scope"), "skills", ["scope"], unique=False)
    op.create_index(op.f("ix_skills_stage"), "skills", ["stage"], unique=False)
    op.create_index(op.f("ix_skills_updated_at"), "skills", ["updated_at"], unique=False)
    op.create_table(
        "system_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("allow_company_link", sa.Boolean(), nullable=False),
        sa.Column("require_admin_approval_publish", sa.Boolean(), nullable=False),
        sa.Column("block_external_share", sa.Boolean(), nullable=False),
        sa.Column("updated_by_id", sa.String(length=32), nullable=True),
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
        sa.CheckConstraint("id = 1", name=op.f("ck_system_policies_singleton")),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_system_policies_updated_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_policies")),
    )
    op.create_index(
        op.f("ix_system_policies_created_at"), "system_policies", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_system_policies_updated_at"), "system_policies", ["updated_at"], unique=False
    )
    op.create_table(
        "user_notification_settings",
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("email_on_research_done", sa.Boolean(), nullable=False),
        sa.Column("email_on_approval_request", sa.Boolean(), nullable=False),
        sa.Column("email_on_comment", sa.Boolean(), nullable=False),
        sa.Column("email_on_share", sa.Boolean(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_notification_settings_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_notification_settings")),
    )
    op.create_index(
        op.f("ix_user_notification_settings_created_at"),
        "user_notification_settings",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_notification_settings_updated_at"),
        "user_notification_settings",
        ["updated_at"],
        unique=False,
    )
    op.create_table(
        "customer_activities",
        sa.Column("customer_id", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=32), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column(
            "meta",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_customer_activities_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_customer_activities_customer_id_customers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_activities")),
    )
    op.create_index(
        "ix_activity_customer_created",
        "customer_activities",
        ["customer_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_customer_activities_created_at"),
        "customer_activities",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_customer_activities_customer_id"),
        "customer_activities",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_customer_activities_kind"), "customer_activities", ["kind"], unique=False
    )
    op.create_index(
        op.f("ix_customer_activities_updated_at"),
        "customer_activities",
        ["updated_at"],
        unique=False,
    )
    op.create_table(
        "customer_facts",
        sa.Column("customer_id", sa.String(length=32), nullable=False),
        sa.Column("topic", sa.String(length=180), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(length=12), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("is_opportunity", sa.Boolean(), nullable=False),
        sa.Column("verified_by_id", sa.String(length=32), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_customer_facts_customer_id_customers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by_id"],
            ["users.id"],
            name=op.f("fk_customer_facts_verified_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_facts")),
    )
    op.create_index(
        op.f("ix_customer_facts_created_at"), "customer_facts", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_customer_facts_customer_id"), "customer_facts", ["customer_id"], unique=False
    )
    op.create_index(op.f("ix_customer_facts_job_id"), "customer_facts", ["job_id"], unique=False)
    op.create_index(
        op.f("ix_customer_facts_updated_at"), "customer_facts", ["updated_at"], unique=False
    )
    op.create_index(op.f("ix_customer_facts_verdict"), "customer_facts", ["verdict"], unique=False)
    op.create_table(
        "customer_handovers",
        sa.Column("customer_id", sa.String(length=32), nullable=False),
        sa.Column("from_user_id", sa.String(length=32), nullable=False),
        sa.Column("to_user_id", sa.String(length=32), nullable=False),
        sa.Column("keep_permission", sa.String(length=12), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_customer_handovers_customer_id_customers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["from_user_id"],
            ["users.id"],
            name=op.f("fk_customer_handovers_from_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_user_id"],
            ["users.id"],
            name=op.f("fk_customer_handovers_to_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_handovers")),
    )
    op.create_index(
        op.f("ix_customer_handovers_created_at"), "customer_handovers", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_customer_handovers_customer_id"),
        "customer_handovers",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_customer_handovers_updated_at"), "customer_handovers", ["updated_at"], unique=False
    )
    op.create_table(
        "customer_solutions",
        sa.Column("customer_id", sa.String(length=32), nullable=False),
        sa.Column("product_id", sa.String(length=32), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("fit_score", sa.Float(), nullable=False),
        sa.Column("expected_impact", sa.Text(), nullable=True),
        sa.Column("pricing_note", sa.Text(), nullable=True),
        sa.Column("package", sa.String(length=64), nullable=True),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column(
            "evidence",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_customer_solutions_customer_id_customers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_customer_solutions_product_id_products"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_solutions")),
    )
    op.create_index(
        op.f("ix_customer_solutions_created_at"), "customer_solutions", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_customer_solutions_customer_id"),
        "customer_solutions",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_customer_solutions_job_id"), "customer_solutions", ["job_id"], unique=False
    )
    op.create_index(
        op.f("ix_customer_solutions_product_id"), "customer_solutions", ["product_id"], unique=False
    )
    op.create_index(
        op.f("ix_customer_solutions_selected"), "customer_solutions", ["selected"], unique=False
    )
    op.create_index(
        op.f("ix_customer_solutions_updated_at"), "customer_solutions", ["updated_at"], unique=False
    )
    op.create_table(
        "customer_sources",
        sa.Column("customer_id", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=180), nullable=True),
        sa.Column("url", sa.String(length=1024), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_customer_sources_customer_id_customers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_sources")),
    )
    op.create_index(
        op.f("ix_customer_sources_created_at"), "customer_sources", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_customer_sources_customer_id"), "customer_sources", ["customer_id"], unique=False
    )
    op.create_index(
        op.f("ix_customer_sources_job_id"), "customer_sources", ["job_id"], unique=False
    )
    op.create_index(
        op.f("ix_customer_sources_updated_at"), "customer_sources", ["updated_at"], unique=False
    )
    op.create_table(
        "knowledge_files",
        sa.Column("kb_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("ext", sa.String(length=8), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("index_error", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uploaded_by_id", sa.String(length=32), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["kb_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_knowledge_files_kb_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_id"],
            ["users.id"],
            name=op.f("fk_knowledge_files_uploaded_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_files")),
        sa.UniqueConstraint("kb_id", "content_hash", name="kb_content_hash"),
    )
    op.create_index("ix_kb_files_status", "knowledge_files", ["kb_id", "status"], unique=False)
    op.create_index(
        op.f("ix_knowledge_files_content_hash"), "knowledge_files", ["content_hash"], unique=False
    )
    op.create_index(
        op.f("ix_knowledge_files_created_at"), "knowledge_files", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_knowledge_files_kb_id"), "knowledge_files", ["kb_id"], unique=False)
    op.create_index(op.f("ix_knowledge_files_status"), "knowledge_files", ["status"], unique=False)
    op.create_index(
        op.f("ix_knowledge_files_updated_at"), "knowledge_files", ["updated_at"], unique=False
    )
    op.create_table(
        "knowledge_sources",
        sa.Column("kb_id", sa.String(length=32), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("doc_count", sa.Integer(), nullable=False),
        sa.Column(
            "meta",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["kb_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_knowledge_sources_kb_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_sources")),
        sa.UniqueConstraint("kb_id", "url", name="kb_source_url"),
    )
    op.create_index(
        op.f("ix_knowledge_sources_created_at"), "knowledge_sources", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_knowledge_sources_kb_id"), "knowledge_sources", ["kb_id"], unique=False
    )
    op.create_index(
        op.f("ix_knowledge_sources_updated_at"), "knowledge_sources", ["updated_at"], unique=False
    )
    op.create_table(
        "proposals",
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("title_norm", sa.String(length=255), nullable=False),
        sa.Column("customer_id", sa.String(length=32), nullable=False),
        sa.Column("template_id", sa.String(length=32), nullable=True),
        sa.Column("owner_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("slide_count", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("tone", sa.String(length=32), nullable=True),
        sa.Column(
            "outline",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "generation_params",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("last_generate_job_id", sa.String(length=32), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_proposals_customer_id_customers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_proposals_owner_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["proposal_templates.id"],
            name=op.f("fk_proposals_template_id_proposal_templates"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposals")),
    )
    op.create_index(op.f("ix_proposals_archived"), "proposals", ["archived"], unique=False)
    op.create_index(op.f("ix_proposals_created_at"), "proposals", ["created_at"], unique=False)
    op.create_index(op.f("ix_proposals_customer_id"), "proposals", ["customer_id"], unique=False)
    op.create_index(op.f("ix_proposals_owner_id"), "proposals", ["owner_id"], unique=False)
    op.create_index(
        "ix_proposals_owner_status", "proposals", ["owner_id", "status", "archived"], unique=False
    )
    op.create_index(op.f("ix_proposals_status"), "proposals", ["status"], unique=False)
    op.create_index(op.f("ix_proposals_template_id"), "proposals", ["template_id"], unique=False)
    op.create_index(op.f("ix_proposals_title_norm"), "proposals", ["title_norm"], unique=False)
    op.create_index("ix_proposals_updated", "proposals", ["archived", "updated_at"], unique=False)
    op.create_index(op.f("ix_proposals_updated_at"), "proposals", ["updated_at"], unique=False)
    op.create_table(
        "skill_test_runs",
        sa.Column("skill_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=12), nullable=True),
        sa.Column(
            "input",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("run_by_id", sa.String(length=32), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["run_by_id"],
            ["users.id"],
            name=op.f("fk_skill_test_runs_run_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["skills.id"],
            name=op.f("fk_skill_test_runs_skill_id_skills"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skill_test_runs")),
    )
    op.create_index(
        op.f("ix_skill_test_runs_created_at"), "skill_test_runs", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_skill_test_runs_skill_id"), "skill_test_runs", ["skill_id"], unique=False
    )
    op.create_index(
        op.f("ix_skill_test_runs_updated_at"), "skill_test_runs", ["updated_at"], unique=False
    )
    op.create_table(
        "skill_versions",
        sa.Column("skill_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=12), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("author_id", sa.String(length=32), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name=op.f("fk_skill_versions_author_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["skills.id"],
            name=op.f("fk_skill_versions_skill_id_skills"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skill_versions")),
        sa.UniqueConstraint("skill_id", "version", name="skill_version"),
    )
    op.create_index(
        op.f("ix_skill_versions_created_at"), "skill_versions", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_skill_versions_skill_id"), "skill_versions", ["skill_id"], unique=False
    )
    op.create_index(
        op.f("ix_skill_versions_updated_at"), "skill_versions", ["updated_at"], unique=False
    )
    op.create_table(
        "template_slides",
        sa.Column("template_id", sa.String(length=32), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("layout", sa.String(length=48), nullable=False),
        sa.Column("image_url", sa.String(length=512), nullable=True),
        sa.Column(
            "placeholders",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("guidance", sa.Text(), nullable=True),
        sa.Column("optional", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["proposal_templates.id"],
            name=op.f("fk_template_slides_template_id_proposal_templates"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_template_slides")),
        sa.UniqueConstraint("template_id", "index", name="template_slide_index"),
    )
    op.create_index(
        op.f("ix_template_slides_created_at"), "template_slides", ["created_at"], unique=False
    )
    op.create_index(
        "ix_template_slides_order", "template_slides", ["template_id", "index"], unique=False
    )
    op.create_index(
        op.f("ix_template_slides_template_id"), "template_slides", ["template_id"], unique=False
    )
    op.create_index(
        op.f("ix_template_slides_updated_at"), "template_slides", ["updated_at"], unique=False
    )
    op.create_table(
        "template_versions",
        sa.Column("template_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=12), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "snapshot",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("author_id", sa.String(length=32), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name=op.f("fk_template_versions_author_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["proposal_templates.id"],
            name=op.f("fk_template_versions_template_id_proposal_templates"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_template_versions")),
        sa.UniqueConstraint("template_id", "version", name="template_version"),
    )
    op.create_index(
        op.f("ix_template_versions_created_at"), "template_versions", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_template_versions_template_id"), "template_versions", ["template_id"], unique=False
    )
    op.create_index(
        op.f("ix_template_versions_updated_at"), "template_versions", ["updated_at"], unique=False
    )
    op.create_table(
        "ai_chat_messages",
        sa.Column("proposal_id", sa.String(length=32), nullable=False),
        sa.Column("slide_id", sa.String(length=32), nullable=True),
        sa.Column("user_id", sa.String(length=32), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "context",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["proposals.id"],
            name=op.f("fk_ai_chat_messages_proposal_id_proposals"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_ai_chat_messages_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_chat_messages")),
    )
    op.create_index(
        op.f("ix_ai_chat_messages_created_at"), "ai_chat_messages", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_ai_chat_messages_proposal_id"), "ai_chat_messages", ["proposal_id"], unique=False
    )
    op.create_index(
        op.f("ix_ai_chat_messages_updated_at"), "ai_chat_messages", ["updated_at"], unique=False
    )
    op.create_index(
        "ix_chat_proposal_created", "ai_chat_messages", ["proposal_id", "created_at"], unique=False
    )
    op.create_table(
        "jobs",
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("customer_id", sa.String(length=32), nullable=True),
        sa.Column("proposal_id", sa.String(length=32), nullable=True),
        sa.Column("kb_id", sa.String(length=32), nullable=True),
        sa.Column("export_id", sa.String(length=32), nullable=True),
        sa.Column("stage", sa.String(length=32), nullable=True),
        sa.Column("stage_label", sa.String(length=255), nullable=True),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column(
            "params",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "result",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "error",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.String(length=32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_jobs_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_jobs_customer_id_customers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["kb_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_jobs_kb_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["proposals.id"],
            name=op.f("fk_jobs_proposal_id_proposals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index(op.f("ix_jobs_created_at"), "jobs", ["created_at"], unique=False)
    op.create_index(op.f("ix_jobs_customer_id"), "jobs", ["customer_id"], unique=False)
    op.create_index(op.f("ix_jobs_export_id"), "jobs", ["export_id"], unique=False)
    op.create_index(op.f("ix_jobs_kb_id"), "jobs", ["kb_id"], unique=False)
    op.create_index(op.f("ix_jobs_kind"), "jobs", ["kind"], unique=False)
    op.create_index("ix_jobs_kind_status", "jobs", ["kind", "status", "created_at"], unique=False)
    op.create_index(op.f("ix_jobs_proposal_id"), "jobs", ["proposal_id"], unique=False)
    op.create_index(op.f("ix_jobs_status"), "jobs", ["status"], unique=False)
    op.create_index(op.f("ix_jobs_updated_at"), "jobs", ["updated_at"], unique=False)
    op.create_table(
        "proposal_approvals",
        sa.Column("proposal_id", sa.String(length=32), nullable=False),
        sa.Column("approver_id", sa.String(length=32), nullable=False),
        sa.Column("requested_by_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.String(length=12), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["approver_id"],
            ["users.id"],
            name=op.f("fk_proposal_approvals_approver_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["proposals.id"],
            name=op.f("fk_proposal_approvals_proposal_id_proposals"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
            name=op.f("fk_proposal_approvals_requested_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposal_approvals")),
    )
    op.create_index(
        op.f("ix_proposal_approvals_approver_id"),
        "proposal_approvals",
        ["approver_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_proposal_approvals_created_at"), "proposal_approvals", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_approvals_proposal_id"),
        "proposal_approvals",
        ["proposal_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_proposal_approvals_status"), "proposal_approvals", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_approvals_updated_at"), "proposal_approvals", ["updated_at"], unique=False
    )
    op.create_table(
        "proposal_exports",
        sa.Column("proposal_id", sa.String(length=32), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("template_id", sa.String(length=32), nullable=True),
        sa.Column("include_appendix", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column(
            "error",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("created_by_id", sa.String(length=32), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_proposal_exports_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["proposals.id"],
            name=op.f("fk_proposal_exports_proposal_id_proposals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposal_exports")),
    )
    op.create_index(
        op.f("ix_proposal_exports_created_at"), "proposal_exports", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_exports_proposal_id"), "proposal_exports", ["proposal_id"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_exports_status"), "proposal_exports", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_exports_updated_at"), "proposal_exports", ["updated_at"], unique=False
    )
    op.create_table(
        "proposal_slides",
        sa.Column("proposal_id", sa.String(length=32), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("layout", sa.String(length=48), nullable=False),
        sa.Column(
            "blocks",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("speaker_notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "citations",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["proposals.id"],
            name=op.f("fk_proposal_slides_proposal_id_proposals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposal_slides")),
        sa.UniqueConstraint("proposal_id", "index", name="slide_index"),
    )
    op.create_index(
        op.f("ix_proposal_slides_created_at"), "proposal_slides", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_slides_proposal_id"), "proposal_slides", ["proposal_id"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_slides_updated_at"), "proposal_slides", ["updated_at"], unique=False
    )
    op.create_index(
        "ix_slides_proposal_index", "proposal_slides", ["proposal_id", "index"], unique=False
    )
    op.create_table(
        "proposal_versions",
        sa.Column("proposal_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=12), nullable=False),
        sa.Column("author_id", sa.String(length=32), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "snapshot",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name=op.f("fk_proposal_versions_author_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["proposals.id"],
            name=op.f("fk_proposal_versions_proposal_id_proposals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposal_versions")),
        sa.UniqueConstraint("proposal_id", "version", name="proposal_version"),
    )
    op.create_index(
        op.f("ix_proposal_versions_created_at"), "proposal_versions", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_versions_proposal_id"), "proposal_versions", ["proposal_id"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_versions_updated_at"), "proposal_versions", ["updated_at"], unique=False
    )
    op.create_table(
        "ai_suggested_changes",
        sa.Column("proposal_id", sa.String(length=32), nullable=False),
        sa.Column("message_id", sa.String(length=32), nullable=True),
        sa.Column("slide_id", sa.String(length=32), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column(
            "patch",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_by_id", sa.String(length=32), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["applied_by_id"],
            ["users.id"],
            name=op.f("fk_ai_suggested_changes_applied_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["ai_chat_messages.id"],
            name=op.f("fk_ai_suggested_changes_message_id_ai_chat_messages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["proposals.id"],
            name=op.f("fk_ai_suggested_changes_proposal_id_proposals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_suggested_changes")),
    )
    op.create_index(
        op.f("ix_ai_suggested_changes_applied"), "ai_suggested_changes", ["applied"], unique=False
    )
    op.create_index(
        op.f("ix_ai_suggested_changes_created_at"),
        "ai_suggested_changes",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_suggested_changes_message_id"),
        "ai_suggested_changes",
        ["message_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_suggested_changes_proposal_id"),
        "ai_suggested_changes",
        ["proposal_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_suggested_changes_updated_at"),
        "ai_suggested_changes",
        ["updated_at"],
        unique=False,
    )
    op.create_table(
        "job_events",
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(length=16), nullable=False),
        sa.Column(
            "data",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_job_events_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_events")),
        sa.UniqueConstraint("job_id", "seq", name="job_event_seq"),
    )
    op.create_index(op.f("ix_job_events_created_at"), "job_events", ["created_at"], unique=False)
    op.create_index(op.f("ix_job_events_job_id"), "job_events", ["job_id"], unique=False)
    op.create_index("ix_job_events_job_seq", "job_events", ["job_id", "seq"], unique=False)
    op.create_index(op.f("ix_job_events_updated_at"), "job_events", ["updated_at"], unique=False)
    op.create_table(
        "proposal_comments",
        sa.Column("proposal_id", sa.String(length=32), nullable=False),
        sa.Column("slide_id", sa.String(length=32), nullable=True),
        sa.Column("parent_id", sa.String(length=32), nullable=True),
        sa.Column("author_id", sa.String(length=32), nullable=True),
        sa.Column("anchor", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("resolved_by_id", sa.String(length=32), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name=op.f("fk_proposal_comments_author_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["proposal_comments.id"],
            name=op.f("fk_proposal_comments_parent_id_proposal_comments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["proposals.id"],
            name=op.f("fk_proposal_comments_proposal_id_proposals"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_id"],
            ["users.id"],
            name=op.f("fk_proposal_comments_resolved_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["slide_id"],
            ["proposal_slides.id"],
            name=op.f("fk_proposal_comments_slide_id_proposal_slides"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposal_comments")),
    )
    op.create_index(
        op.f("ix_proposal_comments_created_at"), "proposal_comments", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_comments_parent_id"), "proposal_comments", ["parent_id"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_comments_proposal_id"), "proposal_comments", ["proposal_id"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_comments_resolved"), "proposal_comments", ["resolved"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_comments_slide_id"), "proposal_comments", ["slide_id"], unique=False
    )
    op.create_index(
        op.f("ix_proposal_comments_updated_at"), "proposal_comments", ["updated_at"], unique=False
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS zbd_unaccent(text)")

    op.drop_index(op.f("ix_proposal_comments_updated_at"), table_name="proposal_comments")
    op.drop_index(op.f("ix_proposal_comments_slide_id"), table_name="proposal_comments")
    op.drop_index(op.f("ix_proposal_comments_resolved"), table_name="proposal_comments")
    op.drop_index(op.f("ix_proposal_comments_proposal_id"), table_name="proposal_comments")
    op.drop_index(op.f("ix_proposal_comments_parent_id"), table_name="proposal_comments")
    op.drop_index(op.f("ix_proposal_comments_created_at"), table_name="proposal_comments")
    op.drop_table("proposal_comments")
    op.drop_index(op.f("ix_job_events_updated_at"), table_name="job_events")
    op.drop_index("ix_job_events_job_seq", table_name="job_events")
    op.drop_index(op.f("ix_job_events_job_id"), table_name="job_events")
    op.drop_index(op.f("ix_job_events_created_at"), table_name="job_events")
    op.drop_table("job_events")
    op.drop_index(op.f("ix_ai_suggested_changes_updated_at"), table_name="ai_suggested_changes")
    op.drop_index(op.f("ix_ai_suggested_changes_proposal_id"), table_name="ai_suggested_changes")
    op.drop_index(op.f("ix_ai_suggested_changes_message_id"), table_name="ai_suggested_changes")
    op.drop_index(op.f("ix_ai_suggested_changes_created_at"), table_name="ai_suggested_changes")
    op.drop_index(op.f("ix_ai_suggested_changes_applied"), table_name="ai_suggested_changes")
    op.drop_table("ai_suggested_changes")
    op.drop_index(op.f("ix_proposal_versions_updated_at"), table_name="proposal_versions")
    op.drop_index(op.f("ix_proposal_versions_proposal_id"), table_name="proposal_versions")
    op.drop_index(op.f("ix_proposal_versions_created_at"), table_name="proposal_versions")
    op.drop_table("proposal_versions")
    op.drop_index("ix_slides_proposal_index", table_name="proposal_slides")
    op.drop_index(op.f("ix_proposal_slides_updated_at"), table_name="proposal_slides")
    op.drop_index(op.f("ix_proposal_slides_proposal_id"), table_name="proposal_slides")
    op.drop_index(op.f("ix_proposal_slides_created_at"), table_name="proposal_slides")
    op.drop_table("proposal_slides")
    op.drop_index(op.f("ix_proposal_exports_updated_at"), table_name="proposal_exports")
    op.drop_index(op.f("ix_proposal_exports_status"), table_name="proposal_exports")
    op.drop_index(op.f("ix_proposal_exports_proposal_id"), table_name="proposal_exports")
    op.drop_index(op.f("ix_proposal_exports_created_at"), table_name="proposal_exports")
    op.drop_table("proposal_exports")
    op.drop_index(op.f("ix_proposal_approvals_updated_at"), table_name="proposal_approvals")
    op.drop_index(op.f("ix_proposal_approvals_status"), table_name="proposal_approvals")
    op.drop_index(op.f("ix_proposal_approvals_proposal_id"), table_name="proposal_approvals")
    op.drop_index(op.f("ix_proposal_approvals_created_at"), table_name="proposal_approvals")
    op.drop_index(op.f("ix_proposal_approvals_approver_id"), table_name="proposal_approvals")
    op.drop_table("proposal_approvals")
    op.drop_index(op.f("ix_jobs_updated_at"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_status"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_proposal_id"), table_name="jobs")
    op.drop_index("ix_jobs_kind_status", table_name="jobs")
    op.drop_index(op.f("ix_jobs_kind"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_kb_id"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_export_id"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_customer_id"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_created_at"), table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_chat_proposal_created", table_name="ai_chat_messages")
    op.drop_index(op.f("ix_ai_chat_messages_updated_at"), table_name="ai_chat_messages")
    op.drop_index(op.f("ix_ai_chat_messages_proposal_id"), table_name="ai_chat_messages")
    op.drop_index(op.f("ix_ai_chat_messages_created_at"), table_name="ai_chat_messages")
    op.drop_table("ai_chat_messages")
    op.drop_index(op.f("ix_template_versions_updated_at"), table_name="template_versions")
    op.drop_index(op.f("ix_template_versions_template_id"), table_name="template_versions")
    op.drop_index(op.f("ix_template_versions_created_at"), table_name="template_versions")
    op.drop_table("template_versions")
    op.drop_index(op.f("ix_template_slides_updated_at"), table_name="template_slides")
    op.drop_index(op.f("ix_template_slides_template_id"), table_name="template_slides")
    op.drop_index("ix_template_slides_order", table_name="template_slides")
    op.drop_index(op.f("ix_template_slides_created_at"), table_name="template_slides")
    op.drop_table("template_slides")
    op.drop_index(op.f("ix_skill_versions_updated_at"), table_name="skill_versions")
    op.drop_index(op.f("ix_skill_versions_skill_id"), table_name="skill_versions")
    op.drop_index(op.f("ix_skill_versions_created_at"), table_name="skill_versions")
    op.drop_table("skill_versions")
    op.drop_index(op.f("ix_skill_test_runs_updated_at"), table_name="skill_test_runs")
    op.drop_index(op.f("ix_skill_test_runs_skill_id"), table_name="skill_test_runs")
    op.drop_index(op.f("ix_skill_test_runs_created_at"), table_name="skill_test_runs")
    op.drop_table("skill_test_runs")
    op.drop_index(op.f("ix_proposals_updated_at"), table_name="proposals")
    op.drop_index("ix_proposals_updated", table_name="proposals")
    op.drop_index(op.f("ix_proposals_title_norm"), table_name="proposals")
    op.drop_index(op.f("ix_proposals_template_id"), table_name="proposals")
    op.drop_index(op.f("ix_proposals_status"), table_name="proposals")
    op.drop_index("ix_proposals_owner_status", table_name="proposals")
    op.drop_index(op.f("ix_proposals_owner_id"), table_name="proposals")
    op.drop_index(op.f("ix_proposals_customer_id"), table_name="proposals")
    op.drop_index(op.f("ix_proposals_created_at"), table_name="proposals")
    op.drop_index(op.f("ix_proposals_archived"), table_name="proposals")
    op.drop_table("proposals")
    op.drop_index(op.f("ix_knowledge_sources_updated_at"), table_name="knowledge_sources")
    op.drop_index(op.f("ix_knowledge_sources_kb_id"), table_name="knowledge_sources")
    op.drop_index(op.f("ix_knowledge_sources_created_at"), table_name="knowledge_sources")
    op.drop_table("knowledge_sources")
    op.drop_index(op.f("ix_knowledge_files_updated_at"), table_name="knowledge_files")
    op.drop_index(op.f("ix_knowledge_files_status"), table_name="knowledge_files")
    op.drop_index(op.f("ix_knowledge_files_kb_id"), table_name="knowledge_files")
    op.drop_index(op.f("ix_knowledge_files_created_at"), table_name="knowledge_files")
    op.drop_index(op.f("ix_knowledge_files_content_hash"), table_name="knowledge_files")
    op.drop_index("ix_kb_files_status", table_name="knowledge_files")
    op.drop_table("knowledge_files")
    op.drop_index(op.f("ix_customer_sources_updated_at"), table_name="customer_sources")
    op.drop_index(op.f("ix_customer_sources_job_id"), table_name="customer_sources")
    op.drop_index(op.f("ix_customer_sources_customer_id"), table_name="customer_sources")
    op.drop_index(op.f("ix_customer_sources_created_at"), table_name="customer_sources")
    op.drop_table("customer_sources")
    op.drop_index(op.f("ix_customer_solutions_updated_at"), table_name="customer_solutions")
    op.drop_index(op.f("ix_customer_solutions_selected"), table_name="customer_solutions")
    op.drop_index(op.f("ix_customer_solutions_product_id"), table_name="customer_solutions")
    op.drop_index(op.f("ix_customer_solutions_job_id"), table_name="customer_solutions")
    op.drop_index(op.f("ix_customer_solutions_customer_id"), table_name="customer_solutions")
    op.drop_index(op.f("ix_customer_solutions_created_at"), table_name="customer_solutions")
    op.drop_table("customer_solutions")
    op.drop_index(op.f("ix_customer_handovers_updated_at"), table_name="customer_handovers")
    op.drop_index(op.f("ix_customer_handovers_customer_id"), table_name="customer_handovers")
    op.drop_index(op.f("ix_customer_handovers_created_at"), table_name="customer_handovers")
    op.drop_table("customer_handovers")
    op.drop_index(op.f("ix_customer_facts_verdict"), table_name="customer_facts")
    op.drop_index(op.f("ix_customer_facts_updated_at"), table_name="customer_facts")
    op.drop_index(op.f("ix_customer_facts_job_id"), table_name="customer_facts")
    op.drop_index(op.f("ix_customer_facts_customer_id"), table_name="customer_facts")
    op.drop_index(op.f("ix_customer_facts_created_at"), table_name="customer_facts")
    op.drop_table("customer_facts")
    op.drop_index(op.f("ix_customer_activities_updated_at"), table_name="customer_activities")
    op.drop_index(op.f("ix_customer_activities_kind"), table_name="customer_activities")
    op.drop_index(op.f("ix_customer_activities_customer_id"), table_name="customer_activities")
    op.drop_index(op.f("ix_customer_activities_created_at"), table_name="customer_activities")
    op.drop_index("ix_activity_customer_created", table_name="customer_activities")
    op.drop_table("customer_activities")
    op.drop_index(
        op.f("ix_user_notification_settings_updated_at"), table_name="user_notification_settings"
    )
    op.drop_index(
        op.f("ix_user_notification_settings_created_at"), table_name="user_notification_settings"
    )
    op.drop_table("user_notification_settings")
    op.drop_index(op.f("ix_system_policies_updated_at"), table_name="system_policies")
    op.drop_index(op.f("ix_system_policies_created_at"), table_name="system_policies")
    op.drop_table("system_policies")
    op.drop_index(op.f("ix_skills_updated_at"), table_name="skills")
    op.drop_index(op.f("ix_skills_stage"), table_name="skills")
    op.drop_index(op.f("ix_skills_scope"), table_name="skills")
    op.drop_index(op.f("ix_skills_owner_id"), table_name="skills")
    op.drop_index(op.f("ix_skills_name_norm"), table_name="skills")
    op.drop_index(op.f("ix_skills_created_at"), table_name="skills")
    op.drop_table("skills")
    op.drop_index(op.f("ix_shares_user_id"), table_name="shares")
    op.drop_index(op.f("ix_shares_updated_at"), table_name="shares")
    op.drop_index(op.f("ix_shares_team_id"), table_name="shares")
    op.drop_index("ix_shares_resource", table_name="shares")
    op.drop_index(op.f("ix_shares_created_at"), table_name="shares")
    op.drop_table("shares")
    op.drop_index(op.f("ix_share_links_updated_at"), table_name="share_links")
    op.drop_index(op.f("ix_share_links_token"), table_name="share_links")
    op.drop_index(op.f("ix_share_links_created_at"), table_name="share_links")
    op.drop_table("share_links")
    op.drop_index(op.f("ix_role_permissions_updated_at"), table_name="role_permissions")
    op.drop_index(op.f("ix_role_permissions_created_at"), table_name="role_permissions")
    op.drop_table("role_permissions")
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_updated_at"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_token_hash"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_created_at"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index(op.f("ix_proposal_templates_updated_at"), table_name="proposal_templates")
    op.drop_index(op.f("ix_proposal_templates_scope"), table_name="proposal_templates")
    op.drop_index(op.f("ix_proposal_templates_owner_id"), table_name="proposal_templates")
    op.drop_index(op.f("ix_proposal_templates_name_norm"), table_name="proposal_templates")
    op.drop_index(op.f("ix_proposal_templates_created_at"), table_name="proposal_templates")
    op.drop_table("proposal_templates")
    op.drop_index("ix_notifications_user_read", table_name="notifications")
    op.drop_index(op.f("ix_notifications_user_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_updated_at"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_type"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_read"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_created_at"), table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(op.f("ix_knowledge_bases_updated_at"), table_name="knowledge_bases")
    op.drop_index(op.f("ix_knowledge_bases_scope"), table_name="knowledge_bases")
    op.drop_index(op.f("ix_knowledge_bases_owner_id"), table_name="knowledge_bases")
    op.drop_index(op.f("ix_knowledge_bases_name_norm"), table_name="knowledge_bases")
    op.drop_index(op.f("ix_knowledge_bases_created_at"), table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
    op.drop_index(op.f("ix_invites_updated_at"), table_name="invites")
    op.drop_index(op.f("ix_invites_token"), table_name="invites")
    op.drop_index(op.f("ix_invites_email"), table_name="invites")
    op.drop_index(op.f("ix_invites_created_at"), table_name="invites")
    op.drop_table("invites")
    op.drop_index(op.f("ix_customers_website_norm"), table_name="customers")
    op.drop_index(op.f("ix_customers_updated_at"), table_name="customers")
    op.drop_index("ix_customers_updated", table_name="customers")
    op.drop_index(op.f("ix_customers_status"), table_name="customers")
    op.drop_index("ix_customers_owner_status", table_name="customers")
    op.drop_index(op.f("ix_customers_owner_id"), table_name="customers")
    op.drop_index(op.f("ix_customers_name_norm"), table_name="customers")
    op.drop_index(op.f("ix_customers_industry"), table_name="customers")
    op.drop_index(op.f("ix_customers_created_at"), table_name="customers")
    op.drop_index(op.f("ix_customers_archived"), table_name="customers")
    op.drop_table("customers")
    op.drop_index(op.f("ix_audit_logs_updated_at"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_search_norm"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_resource_type"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_resource_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_created_at"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_category"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_actor_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_action"), table_name="audit_logs")
    op.drop_index("ix_audit_category_created", table_name="audit_logs")
    op.drop_index("ix_audit_actor_created", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(op.f("ix_ai_config_updated_at"), table_name="ai_config")
    op.drop_index(op.f("ix_ai_config_created_at"), table_name="ai_config")
    op.drop_table("ai_config")
    op.drop_index(op.f("ix_access_requests_updated_at"), table_name="access_requests")
    op.drop_index(op.f("ix_access_requests_type"), table_name="access_requests")
    op.drop_index(op.f("ix_access_requests_status"), table_name="access_requests")
    op.drop_index(op.f("ix_access_requests_resource_type"), table_name="access_requests")
    op.drop_index(op.f("ix_access_requests_resource_id"), table_name="access_requests")
    op.drop_index("ix_access_requests_resource", table_name="access_requests")
    op.drop_index(op.f("ix_access_requests_requester_id"), table_name="access_requests")
    op.drop_index(op.f("ix_access_requests_requester_email"), table_name="access_requests")
    op.drop_index(op.f("ix_access_requests_created_at"), table_name="access_requests")
    op.drop_table("access_requests")
    op.drop_index(op.f("ix_users_updated_at"), table_name="users")
    op.drop_index(op.f("ix_users_team_id"), table_name="users")
    op.drop_index(op.f("ix_users_status"), table_name="users")
    op.drop_index(op.f("ix_users_role"), table_name="users")
    op.drop_index(op.f("ix_users_name_norm"), table_name="users")
    op.drop_index(op.f("ix_users_handle"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_index(op.f("ix_users_created_at"), table_name="users")
    op.drop_table("users")
    op.drop_index(op.f("ix_teams_updated_at"), table_name="teams")
    op.drop_index(op.f("ix_teams_name_norm"), table_name="teams")
    op.drop_index(op.f("ix_teams_created_at"), table_name="teams")
    op.drop_table("teams")
    op.drop_index(op.f("ix_products_updated_at"), table_name="products")
    op.drop_index(op.f("ix_products_status"), table_name="products")
    op.drop_index(op.f("ix_products_name_norm"), table_name="products")
    op.drop_index(op.f("ix_products_group_name"), table_name="products")
    op.drop_index("ix_products_family_group", table_name="products")
    op.drop_index(op.f("ix_products_family"), table_name="products")
    op.drop_index(op.f("ix_products_created_at"), table_name="products")
    op.drop_table("products")
    op.drop_index("ix_otp_email_created", table_name="otp_challenges")
    op.drop_index(op.f("ix_otp_challenges_updated_at"), table_name="otp_challenges")
    op.drop_index(op.f("ix_otp_challenges_email"), table_name="otp_challenges")
    op.drop_index(op.f("ix_otp_challenges_created_at"), table_name="otp_challenges")
    op.drop_table("otp_challenges")
