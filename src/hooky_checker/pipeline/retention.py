from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from hooky_checker.db.models import (
    AggregateSnapshot,
    DataSource,
    IngestionRun,
    RawSnapshot,
    RunStatus,
)


@dataclass(frozen=True)
class PruneResult:
    run_id: str
    raw_rows: int
    aggregate_rows: int


def retention_candidates(
    session: Session,
    retain: int,
    source_id: str | None = None,
) -> list[str]:
    if retain < 2:
        raise ValueError("At least two snapshots must be retained for comparisons")
    source_ids = (
        [source_id]
        if source_id is not None
        else list(session.scalars(select(DataSource.id).order_by(DataSource.id)))
    )
    candidates: list[str] = []
    for current_source_id in source_ids:
        candidates.extend(
            session.scalars(
                select(IngestionRun.id)
                .where(
                    IngestionRun.source_id == current_source_id,
                    IngestionRun.status == RunStatus.SUCCESS,
                    select(RawSnapshot.id)
                    .where(RawSnapshot.run_id == IngestionRun.id)
                    .exists(),
                )
                .order_by(
                    IngestionRun.finished_at.desc(),
                    IngestionRun.snapshot_date.desc(),
                    IngestionRun.id.desc(),
                )
                .offset(retain)
            )
        )
    return candidates


def prune_snapshot_payload(session: Session, run_id: str) -> PruneResult:
    aggregate_result = session.execute(
        delete(AggregateSnapshot).where(AggregateSnapshot.run_id == run_id)
    )
    raw_result = session.execute(delete(RawSnapshot).where(RawSnapshot.run_id == run_id))
    return PruneResult(
        run_id=run_id,
        raw_rows=raw_result.rowcount or 0,
        aggregate_rows=aggregate_result.rowcount or 0,
    )


def replace_same_day_snapshot_payload(
    session: Session,
    source_id: str,
    snapshot_date: date,
    keep_run_id: str,
) -> list[PruneResult]:
    """Keep audit runs, but retain payload only for the latest successful daily snapshot."""
    superseded_run_ids = list(
        session.scalars(
            select(IngestionRun.id).where(
                IngestionRun.source_id == source_id,
                IngestionRun.snapshot_date == snapshot_date,
                IngestionRun.status == RunStatus.SUCCESS,
                IngestionRun.id != keep_run_id,
                select(RawSnapshot.id)
                .where(RawSnapshot.run_id == IngestionRun.id)
                .exists(),
            )
        )
    )
    return [prune_snapshot_payload(session, run_id) for run_id in superseded_run_ids]


def enforce_snapshot_retention(
    session: Session,
    retain: int,
    source_id: str | None = None,
) -> list[PruneResult]:
    return [
        prune_snapshot_payload(session, run_id)
        for run_id in retention_candidates(session, retain, source_id)
    ]
