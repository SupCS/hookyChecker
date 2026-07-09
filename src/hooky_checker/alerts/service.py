import hashlib
import json
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from hooky_checker.checks import CheckResult, find_metric_drops, find_missing_dates
from hooky_checker.db.models import (
    Alert,
    AlertEvent,
    AlertStatus,
    IngestionRun,
    RawSnapshot,
    RunStatus,
)


def _column(frame: pd.DataFrame, *candidates: str) -> str | None:
    lookup = {str(name).strip().lower(): str(name) for name in frame.columns}
    return next((lookup[name.lower()] for name in candidates if name.lower() in lookup), None)


def _frame(rows: list[RawSnapshot]) -> pd.DataFrame:
    frame = pd.DataFrame([row.payload for row in rows])
    frame["data_date"] = [row.data_date for row in rows]
    frame["_row_count"] = 1
    return frame


def _results(previous: pd.DataFrame, current: pd.DataFrame) -> list[CheckResult]:
    results = find_missing_dates(previous, current)
    dimensions = ["data_date"]
    for candidate in ("Channel_Short_Name", "Channel"):
        column = _column(previous, candidate)
        if column and column in current:
            dimensions.append(column)
            break
    for candidate in ("Campaign_Short_Name", "Campaign"):
        column = _column(previous, candidate)
        if column and column in current:
            dimensions.append(column)
            break

    results.extend(
        find_metric_drops(
            previous,
            current,
            dimensions,
            "_row_count",
            relative_threshold=0.20,
            absolute_threshold=10,
        )
    )
    for canonical in ("Conversions", "Revenue"):
        old_column = _column(previous, canonical)
        new_column = _column(current, canonical)
        if not old_column or not new_column:
            continue
        previous[canonical] = pd.to_numeric(previous[old_column], errors="coerce").fillna(0)
        current[canonical] = pd.to_numeric(current[new_column], errors="coerce").fillna(0)
        results.extend(
            find_metric_drops(
                previous,
                current,
                dimensions,
                canonical,
                relative_threshold=0.20,
                absolute_threshold=10,
            )
        )
    return results


def _alert_key(source_id: str, result: CheckResult) -> str:
    identity = json.dumps(
        {
            "source_id": source_id,
            "check_type": result.check_type,
            "dimensions": result.dimensions,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def evaluate_snapshot(session: Session, run: IngestionRun) -> int:
    previous_run = session.scalar(
        select(IngestionRun)
        .where(
            IngestionRun.source_id == run.source_id,
            IngestionRun.status == RunStatus.SUCCESS,
            IngestionRun.id != run.id,
        )
        .order_by(IngestionRun.finished_at.desc())
        .limit(1)
    )
    if previous_run is None:
        return 0

    previous_rows = list(
        session.scalars(select(RawSnapshot).where(RawSnapshot.run_id == previous_run.id))
    )
    current_rows = list(session.scalars(select(RawSnapshot).where(RawSnapshot.run_id == run.id)))
    results = _results(_frame(previous_rows), _frame(current_rows))
    detected_keys: set[str] = set()
    now = datetime.now(UTC)

    for result in results:
        key = _alert_key(run.source_id or "", result)
        detected_keys.add(key)
        alert = session.scalar(select(Alert).where(Alert.alert_key == key))
        if alert is None:
            alert = Alert(
                alert_key=key,
                check_type=result.check_type,
                severity=result.severity,
                status=AlertStatus.OPEN,
                title=result.title,
                dimensions={"source_id": run.source_id, **result.dimensions},
            )
            session.add(alert)
            session.flush()
        else:
            alert.status = AlertStatus.ONGOING
            alert.last_seen_at = now
            alert.recovery_count = 0
        session.add(
            AlertEvent(
                alert_id=alert.id,
                run_id=run.id,
                status=alert.status,
                expected=result.expected,
                actual=result.actual,
                evidence=result.evidence,
            )
        )

    active_alerts = list(
        session.scalars(
            select(Alert).where(
                Alert.dimensions["source_id"].as_string() == run.source_id,
                Alert.status.in_(
                    [AlertStatus.OPEN, AlertStatus.ONGOING, AlertStatus.RECOVERED]
                ),
            )
        )
    )
    for alert in active_alerts:
        if alert.alert_key in detected_keys:
            continue
        alert.recovery_count += 1
        alert.status = (
            AlertStatus.RESOLVED if alert.recovery_count >= 2 else AlertStatus.RECOVERED
        )
        session.add(
            AlertEvent(alert_id=alert.id, run_id=run.id, status=alert.status, evidence={})
        )
    return len(results)
