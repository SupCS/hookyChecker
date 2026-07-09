import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class AlertStatus(StrEnum):
    OPEN = "OPEN"
    ONGOING = "ONGOING"
    RECOVERED = "RECOVERED"
    RESOLVED = "RESOLVED"


class DataSource(Base):
    __tablename__ = "data_source"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    sheet_url: Mapped[str | None] = mapped_column(String(1000))
    worksheet_name: Mapped[str] = mapped_column(String(200), nullable=False)
    ingest_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class IngestionRun(Base):
    __tablename__ = "ingestion_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id: Mapped[str | None] = mapped_column(ForeignKey("data_source.id"), index=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_row_count: Mapped[int | None] = mapped_column(Integer)
    checksum: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

    raw_rows: Mapped[list["RawSnapshot"]] = relationship(cascade="all, delete-orphan")
    aggregates: Mapped[list["AggregateSnapshot"]] = relationship(cascade="all, delete-orphan")


class RawSnapshot(Base):
    __tablename__ = "raw_snapshot"
    __table_args__ = (
        UniqueConstraint("run_id", "row_number", name="uq_raw_run_row"),
        Index("ix_raw_run_data_date", "run_id", "data_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("ingestion_run.id"), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    data_date: Mapped[date | None] = mapped_column(Date)
    row_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class AggregateSnapshot(Base):
    __tablename__ = "aggregate_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "grain", "dimension_key", name="uq_aggregate_run_grain_dimension"
        ),
        Index("ix_aggregate_run_grain", "run_id", "grain"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("ingestion_run.id"), nullable=False)
    grain: Mapped[str] = mapped_column(String(100), nullable=False)
    dimension_key: Mapped[str] = mapped_column(String(500), nullable=False)
    dimensions: Mapped[dict] = mapped_column(JSON, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    impressions: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=0)
    clicks: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=0)
    cost: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=0)
    conversions: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=0)
    revenue: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=0)


class Alert(Base):
    __tablename__ = "alert"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    alert_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    check_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[AlertStatus] = mapped_column(Enum(AlertStatus), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    dimensions: Mapped[dict] = mapped_column(JSON, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    recovery_count: Mapped[int] = mapped_column(Integer, default=0)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(200))

    events: Mapped[list["AlertEvent"]] = relationship(cascade="all, delete-orphan")


class AlertEvent(Base):
    __tablename__ = "alert_event"
    __table_args__ = (Index("ix_alert_event_alert_created", "alert_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alert.id"), nullable=False)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_run.id"))
    status: Mapped[AlertStatus] = mapped_column(Enum(AlertStatus), nullable=False)
    expected: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    actual: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
