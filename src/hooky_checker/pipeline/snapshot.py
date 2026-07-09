import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from hooky_checker.alerts import evaluate_snapshot
from hooky_checker.db.models import IngestionRun, RawSnapshot, RunStatus

DATE_COLUMN_CANDIDATES = ("Date", "date", "data_date")


def _json_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _parse_data_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _canonical_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {str(key).strip(): _json_value(value) for key, value in row.items()}


def dataframe_from_values(values: list[list[Any]]) -> pd.DataFrame:
    if not values:
        raise ValueError("Получен пустой snapshot")
    headers = [str(value).strip() for value in values[0]]
    if not headers or not any(headers):
        raise ValueError("В первой строке отсутствуют заголовки")
    if len(headers) != len(set(headers)):
        raise ValueError("В таблице есть дублирующиеся названия колонок")
    width = len(headers)
    rows = [row[:width] + [None] * max(0, width - len(row)) for row in values[1:]]
    frame = pd.DataFrame(rows, columns=headers).dropna(how="all").reset_index(drop=True)
    if frame.empty:
        raise ValueError("В snapshot нет строк данных")
    return frame


def publish_push_snapshot(
    session: Session,
    source_id: str,
    values: list[list[Any]],
    snapshot_date: date | None = None,
) -> tuple[IngestionRun, bool]:
    frame = dataframe_from_values(values)
    payloads = [_canonical_payload(row) for row in frame.to_dict(orient="records")]
    canonical = json.dumps(payloads, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    effective_date = snapshot_date or datetime.now(UTC).date()

    latest_same_day = session.scalar(
        select(IngestionRun).where(
            IngestionRun.source_id == source_id,
            IngestionRun.snapshot_date == effective_date,
            IngestionRun.status == RunStatus.SUCCESS,
        ).order_by(IngestionRun.finished_at.desc()).limit(1)
    )
    if latest_same_day and latest_same_day.checksum == checksum:
        return latest_same_day, False

    run = IngestionRun(
        source_id=source_id,
        snapshot_date=effective_date,
        status=RunStatus.RUNNING,
        source_row_count=len(frame),
        checksum=checksum,
    )
    session.add(run)
    session.flush()

    date_column = next((name for name in DATE_COLUMN_CANDIDATES if name in frame.columns), None)
    for row_number, payload in enumerate(payloads, start=2):
        row_json = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        session.add(
            RawSnapshot(
                run_id=run.id,
                row_number=row_number,
                data_date=_parse_data_date(payload.get(date_column)) if date_column else None,
                row_fingerprint=hashlib.sha256(row_json.encode("utf-8")).hexdigest(),
                payload=payload,
            )
        )

    run.status = RunStatus.SUCCESS
    run.finished_at = datetime.now(UTC)
    session.flush()
    evaluate_snapshot(session, run)
    return run, True
