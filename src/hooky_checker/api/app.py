import secrets
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from hooky_checker.config import get_settings
from hooky_checker.db.models import (
    Alert,
    AlertEvent,
    AlertStatus,
    DataSource,
    IngestionRun,
    RawSnapshot,
    RunStatus,
)
from hooky_checker.db.session import SessionFactory, create_schema
from hooky_checker.pipeline import publish_push_snapshot
from hooky_checker.security import generate_ingest_token, hash_ingest_token


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_schema()
    yield


app = FastAPI(title="Hooky Checker API", version="0.1.0", lifespan=lifespan)
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


class SnapshotPayload(BaseModel):
    values: list[list[Any]] = Field(min_length=2)


def get_session():
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def dashboard_context(session: Session) -> dict[str, Any]:
    latest_run = session.scalar(
        select(IngestionRun)
        .where(IngestionRun.status == RunStatus.SUCCESS)
        .order_by(IngestionRun.finished_at.desc())
        .limit(1)
    )
    active_count = session.scalar(
        select(func.count(Alert.id)).where(
            Alert.status.in_([AlertStatus.OPEN, AlertStatus.ONGOING, AlertStatus.RECOVERED])
        )
    )
    critical_count = session.scalar(
        select(func.count(Alert.id)).where(
            Alert.status.in_([AlertStatus.OPEN, AlertStatus.ONGOING]),
            Alert.severity == "CRITICAL",
        )
    )
    alerts = list(session.scalars(select(Alert).order_by(Alert.last_seen_at.desc()).limit(300)))
    active_alert_rows = []
    historical_alert_rows = []
    for alert in alerts:
        latest_event = session.scalar(
            select(AlertEvent)
            .where(AlertEvent.alert_id == alert.id)
            .order_by(AlertEvent.created_at.desc())
            .limit(1)
        )
        row = {"alert": alert, "event": latest_event}
        if alert.status in (AlertStatus.OPEN, AlertStatus.ONGOING):
            active_alert_rows.append(row)
        else:
            historical_alert_rows.append(row)
    return {
        "latest_run": latest_run,
        "active_count": active_count or 0,
        "critical_count": critical_count or 0,
        "alert_rows": active_alert_rows,
        "historical_alert_rows": historical_alert_rows,
        "sources": list(session.scalars(select(DataSource).order_by(DataSource.name))),
        "public_api_url": get_settings().effective_public_api_url,
    }


def request_public_url(request: Request) -> str:
    forwarded_proto = request.headers.get("x-forwarded-proto")
    scheme = forwarded_proto.split(",", 1)[0].strip() if forwarded_proto else request.url.scheme
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{scheme}://{host}".rstrip("/")


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=dashboard_context(session),
    )


@app.post("/sources", response_class=HTMLResponse)
def create_source(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    name: Annotated[str, Form()],
    worksheet_name: Annotated[str, Form()] = "All_Data",
) -> HTMLResponse:
    context = dashboard_context(session)
    if not name.strip() or not worksheet_name.strip():
        context["source_error"] = "Заполните название проекта и вкладки."
    else:
        token = generate_ingest_token()
        try:
            source = DataSource(
                name=name.strip(),
                worksheet_name=worksheet_name.strip(),
                ingest_token_hash=hash_ingest_token(token),
            )
            session.add(source)
            session.flush()
            context = dashboard_context(session)
            context["public_api_url"] = request_public_url(request)
            context["new_source"] = source
            context["new_token"] = token
        except IntegrityError:
            session.rollback()
            context = dashboard_context(session)
            context["source_error"] = "Проект с таким названием уже существует."
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=context,
    )


@app.post("/sources/{source_id}/rotate-token", response_class=HTMLResponse)
def rotate_source_token(
    source_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    source = session.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    token = generate_ingest_token()
    source.ingest_token_hash = hash_ingest_token(token)
    session.flush()
    context = dashboard_context(session)
    context["public_api_url"] = request_public_url(request)
    context["new_source"] = source
    context["new_token"] = token
    context["token_rotated"] = True
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=context,
    )


@app.get("/sources/{source_id}", response_class=HTMLResponse)
def source_detail(
    source_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    source = session.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    runs = list(
        session.scalars(
            select(IngestionRun)
            .where(IngestionRun.source_id == source.id)
            .order_by(IngestionRun.started_at.desc())
            .limit(30)
        )
    )
    latest_run = next((run for run in runs if run.status == RunStatus.SUCCESS), None)
    first_row = (
        session.scalar(
            select(RawSnapshot)
            .where(RawSnapshot.run_id == latest_run.id)
            .order_by(RawSnapshot.row_number)
            .limit(1)
        )
        if latest_run
        else None
    )
    columns = list(first_row.payload) if first_row else []
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except ValueError:
        page = 1
    try:
        page_size = int(request.query_params.get("page_size", "100"))
    except ValueError:
        page_size = 100
    page_size = page_size if page_size in {50, 100, 250, 500} else 100
    filters = {
        column: value.strip()
        for index, column in enumerate(columns)
        if (value := request.query_params.get(f"f{index}", "")).strip()
    }

    raw_rows: list[RawSnapshot] = []
    filtered_count = 0
    if latest_run:
        conditions = [RawSnapshot.run_id == latest_run.id]
        conditions.extend(
            RawSnapshot.payload[column].as_string().ilike(f"%{value}%")
            for column, value in filters.items()
        )
        filtered_count = session.scalar(
            select(func.count(RawSnapshot.id)).where(*conditions)
        ) or 0
        max_page = max(1, (filtered_count + page_size - 1) // page_size)
        page = min(page, max_page)
        raw_rows = list(
            session.scalars(
                select(RawSnapshot)
                .where(*conditions)
                .order_by(RawSnapshot.row_number)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
    else:
        max_page = 1
    return templates.TemplateResponse(
        request=request,
        name="source_detail.html",
        context={
            "source": source,
            "runs": runs,
            "latest_run": latest_run,
            "raw_rows": raw_rows,
            "columns": columns,
            "filters": filters,
            "filtered_count": filtered_count,
            "page": page,
            "page_size": page_size,
            "max_page": max_page,
            "query_without_page": urlencode(
                [
                    (f"f{index}", value)
                    for index, column in enumerate(columns)
                    if (value := filters.get(column))
                ]
            ),
        },
    )


def _rows_for_alert(
    session: Session,
    run_id: str | None,
    dimensions: dict[str, Any],
) -> list[RawSnapshot]:
    if run_id is None:
        return []
    conditions = [RawSnapshot.run_id == run_id]
    for key, value in dimensions.items():
        if key == "source_id":
            continue
        if key == "data_date":
            parsed_date = date.fromisoformat(value) if isinstance(value, str) else value
            conditions.append(RawSnapshot.data_date == parsed_date)
        else:
            conditions.append(RawSnapshot.payload[key].as_string() == str(value))
    return list(
        session.scalars(
            select(RawSnapshot)
            .where(*conditions)
            .order_by(RawSnapshot.row_number)
            .limit(500)
        )
    )


@app.get("/alerts/{alert_id}", response_class=HTMLResponse)
def alert_detail(
    alert_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    events = list(
        session.scalars(
            select(AlertEvent)
            .where(AlertEvent.alert_id == alert.id)
            .order_by(AlertEvent.created_at.desc())
        )
    )
    latest_event = events[0] if events else None
    current_run = (
        session.get(IngestionRun, latest_event.run_id)
        if latest_event and latest_event.run_id
        else None
    )
    previous_run = (
        session.scalar(
            select(IngestionRun)
            .where(
                IngestionRun.source_id == current_run.source_id,
                IngestionRun.status == RunStatus.SUCCESS,
                IngestionRun.finished_at < current_run.finished_at,
            )
            .order_by(IngestionRun.finished_at.desc())
            .limit(1)
        )
        if current_run
        else None
    )
    source_id = alert.dimensions.get("source_id")
    source = session.get(DataSource, source_id) if source_id else None
    previous_rows = _rows_for_alert(
        session,
        previous_run.id if previous_run else None,
        alert.dimensions,
    )
    current_rows = _rows_for_alert(
        session,
        current_run.id if current_run else None,
        alert.dimensions,
    )
    all_rows = previous_rows or current_rows
    columns = list(all_rows[0].payload) if all_rows else []
    changes = (
        latest_event.evidence.get("changes", [])
        if latest_event and latest_event.evidence
        else []
    )
    metrics = [change["metric"] for change in changes]
    if not metrics:
        metrics = [alert.check_type.removesuffix("_drop")]
    previous_by_number = {row.row_number: row for row in previous_rows}
    current_by_number = {row.row_number: row for row in current_rows}
    changed_row_numbers = {
        row_number
        for row_number in previous_by_number.keys() | current_by_number.keys()
        if previous_by_number.get(row_number) is None
        or current_by_number.get(row_number) is None
        or any(
            previous_by_number[row_number].payload.get(metric)
            != current_by_number[row_number].payload.get(metric)
            for metric in metrics
        )
    }
    return templates.TemplateResponse(
        request=request,
        name="alert_detail.html",
        context={
            "alert": alert,
            "events": events,
            "event": latest_event,
            "source": source,
            "previous_run": previous_run,
            "current_run": current_run,
            "previous_rows": previous_rows,
            "current_rows": current_rows,
            "columns": columns,
            "metrics": metrics,
            "changes": changes,
            "changed_row_numbers": changed_row_numbers,
        },
    )


@app.post("/alerts/{alert_id}/resolve")
def resolve_alert(
    alert_id: str,
    session: Annotated[Session, Depends(get_session)],
    reason: Annotated[str, Form()],
    resolved_by: Annotated[str, Form()] = "Manual",
) -> RedirectResponse:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    clean_reason = reason.strip()
    if not clean_reason:
        raise HTTPException(status_code=422, detail="Resolution reason is required")
    latest_event = session.scalar(
        select(AlertEvent)
        .where(AlertEvent.alert_id == alert.id)
        .order_by(AlertEvent.created_at.desc())
        .limit(1)
    )
    alert.status = AlertStatus.RESOLVED
    alert.recovery_count = 2
    alert.acknowledged_at = datetime.now(UTC)
    alert.acknowledged_by = resolved_by.strip() or "Manual"
    session.add(
        AlertEvent(
            alert_id=alert.id,
            run_id=latest_event.run_id if latest_event else None,
            status=AlertStatus.RESOLVED,
            evidence={
                "resolution_type": "accepted_as_expected",
                "reason": clean_reason,
                "resolved_by": alert.acknowledged_by,
            },
        )
    )
    return RedirectResponse(url=f"/alerts/{alert.id}", status_code=303)


@app.post("/api/v1/snapshots", status_code=status.HTTP_201_CREATED)
def ingest_snapshot(
    payload: SnapshotPayload,
    session: Annotated[Session, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    token_hash = hash_ingest_token(token)
    source = session.scalar(
        select(DataSource).where(
            DataSource.ingest_token_hash == token_hash,
            DataSource.enabled.is_(True),
        )
    )
    if source is None or not secrets.compare_digest(
        source.ingest_token_hash or "",
        token_hash,
    ):
        raise HTTPException(status_code=401, detail="Invalid bearer token")

    run, created = publish_push_snapshot(session, source.id, payload.values)
    return {
        "run_id": run.id,
        "source": source.name,
        "row_count": run.source_row_count,
        "created": created,
    }
