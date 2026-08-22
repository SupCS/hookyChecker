"""Persist login attempts for rate limiting."""

import sqlalchemy as sa
from alembic import op

revision = "0003_login_security"
down_revision = "0002_auth_dashboard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_attempt",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("ip_address", sa.String(64), nullable=False),
        sa.Column("successful", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_login_attempt_lookup", "login_attempt", ["email", "ip_address", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("login_attempt")
