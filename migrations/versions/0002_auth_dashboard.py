"""Authentication and dashboard configuration."""

import sqlalchemy as sa
from alembic import op

revision = "0002_auth_dashboard"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

user_role = sa.Enum("VIEWER", "EDITOR", "ADMIN", name="userrole")


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(300), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_app_user_email", "app_user", ["email"], unique=True)
    op.create_table(
        "auth_session",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_session_user_id", "auth_session", ["user_id"])
    op.create_index("ix_auth_session_expires_at", "auth_session", ["expires_at"])
    op.create_table(
        "dashboard_config",
        sa.Column("source_id", sa.String(36), sa.ForeignKey("data_source.id"), primary_key=True),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(36), sa.ForeignKey("app_user.id")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "dashboard_config_revision",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("data_source.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("changed_by", sa.String(36), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "version", name="uq_dashboard_revision_version"),
    )
    op.create_index(
        "ix_dashboard_config_revision_source_id", "dashboard_config_revision", ["source_id"]
    )


def downgrade() -> None:
    op.drop_table("dashboard_config_revision")
    op.drop_table("dashboard_config")
    op.drop_table("auth_session")
    op.drop_table("app_user")
