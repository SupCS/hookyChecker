from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from hooky_checker.db.models import Base, DataSource, IngestionRun, RawSnapshot, RunStatus
from hooky_checker.pipeline.retention import enforce_snapshot_retention, retention_candidates


def test_retains_latest_fourteen_successful_snapshot_payloads() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        source = DataSource(name="Retention", worksheet_name="Data")
        session.add(source)
        session.flush()
        runs = []
        for offset in range(16):
            run = IngestionRun(
                source_id=source.id,
                snapshot_date=date(2026, 1, 1) + timedelta(days=offset),
                status=RunStatus.SUCCESS,
                finished_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=offset),
            )
            session.add(run)
            session.flush()
            runs.append(run)
            session.add(
                RawSnapshot(
                    run_id=run.id,
                    row_number=2,
                    row_fingerprint=str(offset).zfill(64),
                    payload={"value": offset},
                )
            )
        session.flush()

        results = enforce_snapshot_retention(session, retain=14, source_id=source.id)

        assert sum(result.raw_rows for result in results) == 2
        remaining_run_ids = set(session.scalars(select(RawSnapshot.run_id)))
        assert remaining_run_ids == {run.id for run in runs[-14:]}
        assert session.scalar(select(func.count(IngestionRun.id))) == 16


def test_retention_requires_two_snapshots_for_comparison() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session, pytest.raises(ValueError):
        retention_candidates(session, retain=1)
