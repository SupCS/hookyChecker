"""Original Hooky Checker schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

run_status = sa.Enum("RUNNING", "SUCCESS", "FAILED", name="runstatus")
alert_status = sa.Enum("OPEN", "ONGOING", "RECOVERED", "RESOLVED", name="alertstatus")


def upgrade() -> None:
    op.create_table(
        "data_source",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("sheet_url", sa.String(1000)),
        sa.Column("worksheet_name", sa.String(200), nullable=False),
        sa.Column("ingest_token_hash", sa.String(64), unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ingestion_run",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("data_source.id")),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("status", run_status, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("source_row_count", sa.Integer()),
        sa.Column("checksum", sa.String(64)),
        sa.Column("error_message", sa.Text()),
    )
    op.create_index("ix_ingestion_run_source_id", "ingestion_run", ["source_id"])
    op.create_index("ix_ingestion_run_snapshot_date", "ingestion_run", ["snapshot_date"])
    op.create_table(
        "raw_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("ingestion_run.id"), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("data_date", sa.Date()),
        sa.Column("row_fingerprint", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("run_id", "row_number", name="uq_raw_run_row"),
    )
    op.create_index("ix_raw_run_data_date", "raw_snapshot", ["run_id", "data_date"])
    op.create_table(
        "aggregate_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("ingestion_run.id"), nullable=False),
        sa.Column("grain", sa.String(100), nullable=False),
        sa.Column("dimension_key", sa.String(500), nullable=False),
        sa.Column("dimensions", sa.JSON(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("impressions", sa.Numeric(20, 4)),
        sa.Column("clicks", sa.Numeric(20, 4)),
        sa.Column("cost", sa.Numeric(20, 4)),
        sa.Column("conversions", sa.Numeric(20, 4)),
        sa.Column("revenue", sa.Numeric(20, 4)),
        sa.UniqueConstraint(
            "run_id", "grain", "dimension_key", name="uq_aggregate_run_grain_dimension"
        ),
    )
    op.create_index("ix_aggregate_run_grain", "aggregate_snapshot", ["run_id", "grain"])
    op.create_table(
        "alert",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("alert_key", sa.String(64), nullable=False, unique=True),
        sa.Column("check_type", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", alert_status, nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("dimensions", sa.JSON(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recovery_count", sa.Integer(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_by", sa.String(200)),
    )
    op.create_table(
        "alert_event",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("alert_id", sa.String(36), sa.ForeignKey("alert.id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("ingestion_run.id")),
        sa.Column("status", alert_status, nullable=False),
        sa.Column("expected", sa.Numeric(20, 4)),
        sa.Column("actual", sa.Numeric(20, 4)),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_alert_event_alert_created", "alert_event", ["alert_id", "created_at"])


def downgrade() -> None:
    op.drop_table("alert_event")
    op.drop_table("alert")
    op.drop_table("aggregate_snapshot")
    op.drop_table("raw_snapshot")
    op.drop_table("ingestion_run")
    op.drop_table("data_source")
