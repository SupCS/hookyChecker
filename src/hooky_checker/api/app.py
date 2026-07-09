import secrets
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import HTMLResponse
from starlette.templating import Jinja2Templates

from hooky_checker.config import get_settings
from hooky_checker.db.models import Alert, AlertStatus, DataSource, IngestionRun, RunStatus
from hooky_checker.db.session import SessionFactory, create_schema
from hooky_checker.pipeline import publish_push_snapshot
from hooky_checker.security import generate_ingest_token, hash_ingest_token

app = FastAPI(title="Hooky Checker API", version="0.1.0")
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


@app.on_event("startup")
def startup() -> None:
    create_schema()


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
    return {
        "latest_run": latest_run,
        "active_count": active_count or 0,
        "critical_count": critical_count or 0,
        "alerts": list(
            session.scalars(select(Alert).order_by(Alert.last_seen_at.desc()).limit(200))
        ),
        "sources": list(session.scalars(select(DataSource).order_by(DataSource.name))),
        "public_api_url": get_settings().effective_public_api_url,
    }


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
