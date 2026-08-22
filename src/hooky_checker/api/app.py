import secrets
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.templating import Jinja2Templates

from hooky_checker.auth import (
    COOKIE_NAME,
    CSRF_COOKIE_NAME,
    csrf_token,
    current_user,
    hash_password,
    normalize_email,
    require_csrf,
    require_role,
    session_id,
    verify_password,
)
from hooky_checker.config import get_settings
from hooky_checker.dashboard import dashboard_payload
from hooky_checker.db.models import (
    Alert,
    AlertEvent,
    AlertStatus,
    AuthSession,
    DashboardConfig,
    DashboardConfigRevision,
    DataSource,
    IngestionRun,
    LoginAttempt,
    RawSnapshot,
    RunStatus,
    User,
    UserRole,
)
from hooky_checker.db.session import SessionFactory, migrate_database
from hooky_checker.pipeline import publish_push_snapshot
from hooky_checker.security import generate_ingest_token, hash_ingest_token


@asynccontextmanager
async def lifespan(_: FastAPI):
    migrate_database()
    settings = get_settings()
    if settings.admin_email and settings.admin_password:
        with SessionFactory.begin() as session:
            email = normalize_email(settings.admin_email)
            admin = session.scalar(select(User).where(User.email == email))
            if admin is None:
                session.add(
                    User(
                        email=email,
                        display_name="Administrator",
                        password_hash=hash_password(settings.admin_password),
                        role=UserRole.ADMIN,
                    )
                )
    yield


app = FastAPI(title="Hooky Checker API", version="0.1.0", lifespan=lifespan)
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@app.middleware("http")
async def csrf_cookie(request: Request, call_next):
    token = csrf_token(request)
    response = await call_next(request)
    if request.cookies.get(CSRF_COOKIE_NAME) != token:
        forwarded_proto = request.headers.get("x-forwarded-proto", "")
        response.set_cookie(
            CSRF_COOKIE_NAME,
            token,
            httponly=False,
            secure=request.url.scheme == "https" or forwarded_proto.split(",", 1)[0] == "https",
            samesite="strict",
        )
    return response


class SnapshotPayload(BaseModel):
    values: list[list[Any]] = Field(min_length=2)


class WidgetConfig(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    type: str = Field(pattern=r"^(kpi|table)$")
    title: str = Field(min_length=1, max_length=120)
    metrics: list[str] = Field(min_length=1, max_length=30)
    breakdown: str | None = Field(default=None, pattern=r"^(campaign|channel|location|month)$")
    format: str = Field(default="auto", pattern=r"^(auto|currency|percent|number)$")

    @model_validator(mode="after")
    def validate_breakdown(self):
        if self.type == "table" and self.breakdown is None:
            raise ValueError("Table widgets require a breakdown")
        if self.type == "kpi":
            self.breakdown = None
        return self


class DashboardConfigPayload(BaseModel):
    version: int = Field(ge=0)
    widgets: list[WidgetConfig] = Field(max_length=50)


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


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name="login.html", context={"csrf_token": csrf_token(request)}
    )


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    csrf: Annotated[str, Form()],
) -> Response:
    require_csrf(request, csrf)
    normalized_email = normalize_email(email)
    ip_address = client_ip(request)
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.login_window_minutes)
    session.execute(delete(LoginAttempt).where(LoginAttempt.created_at < cutoff))
    email_failed_count = session.scalar(
        select(func.count(LoginAttempt.id)).where(
            LoginAttempt.successful.is_(False),
            LoginAttempt.created_at >= cutoff,
            LoginAttempt.email == normalized_email,
        )
    )
    ip_failed_count = session.scalar(
        select(func.count(LoginAttempt.id)).where(
            LoginAttempt.successful.is_(False),
            LoginAttempt.created_at >= cutoff,
            LoginAttempt.ip_address == ip_address,
        )
    )
    if (email_failed_count or 0) >= settings.login_max_attempts or (
        ip_failed_count or 0
    ) >= settings.login_max_attempts * 5:
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
    user = session.scalar(select(User).where(User.email == normalized_email))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        session.add(
            LoginAttempt(
                email=normalized_email,
                ip_address=ip_address,
                successful=False,
            )
        )
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": "Неверный email или пароль.",
                "email": email,
                "csrf_token": csrf_token(request),
            },
            status_code=401,
        )
    session.execute(
        delete(LoginAttempt).where(
            or_(LoginAttempt.email == normalized_email, LoginAttempt.ip_address == ip_address)
        )
    )
    token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            id=session_id(token),
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(days=get_settings().auth_session_days),
        )
    )
    user.last_login_at = datetime.now(UTC)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=get_settings().auth_session_days * 86400,
        httponly=True,
        secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https",
        samesite="lax",
    )
    return response


@app.post("/logout")
def logout(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    csrf: Annotated[str, Form()],
) -> RedirectResponse:
    require_csrf(request, csrf)
    token = request.cookies.get(COOKIE_NAME)
    if token and (auth_session := session.get(AuthSession, session_id(token))):
        session.delete(auth_session)
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@app.get("/admin/users", response_class=HTMLResponse)
def users_admin(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    q: str = "",
) -> HTMLResponse:
    admin = require_role(request, session, UserRole.ADMIN)
    query = select(User).order_by(User.email)
    if q.strip():
        pattern = f"%{q.strip()}%"
        query = query.where((User.email.ilike(pattern)) | (User.display_name.ilike(pattern)))
    return templates.TemplateResponse(
        request=request,
        name="users.html",
        context={
            "current_user": admin,
            "users": list(session.scalars(query)),
            "q": q,
            "csrf_token": csrf_token(request),
        },
    )


@app.post("/admin/users")
def create_user(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    email: Annotated[str, Form()],
    display_name: Annotated[str, Form()],
    password: Annotated[str, Form()],
    csrf: Annotated[str, Form()],
) -> RedirectResponse:
    require_role(request, session, UserRole.ADMIN)
    require_csrf(request, csrf)
    try:
        session.add(
            User(
                email=normalize_email(email),
                display_name=display_name.strip() or email.strip(),
                password_hash=hash_password(password),
                role=UserRole.VIEWER,
            )
        )
        session.flush()
    except (IntegrityError, ValueError) as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/role")
def update_user_role(
    user_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    role: Annotated[str, Form()],
    csrf: Annotated[str, Form()],
) -> RedirectResponse:
    admin = require_role(request, session, UserRole.ADMIN)
    require_csrf(request, csrf)
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot change your own admin role")
    if role not in {UserRole.VIEWER.value, UserRole.EDITOR.value}:
        raise HTTPException(status_code=400, detail="Only viewer/editor may be assigned")
    user.role = UserRole(role)
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/active")
def update_user_active(
    user_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    active: Annotated[str, Form()],
    csrf: Annotated[str, Form()],
) -> RedirectResponse:
    admin = require_role(request, session, UserRole.ADMIN)
    require_csrf(request, csrf)
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot disable your own account")
    user.is_active = active == "true"
    if not user.is_active:
        session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/reset-password")
def reset_user_password(
    user_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    new_password: Annotated[str, Form()],
    csrf: Annotated[str, Form()],
) -> RedirectResponse:
    require_role(request, session, UserRole.ADMIN)
    require_csrf(request, csrf)
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        user.password_hash = hash_password(new_password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    return RedirectResponse(url="/admin/users", status_code=303)


@app.get("/account/password", response_class=HTMLResponse)
def password_page(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    user = require_role(request, session, UserRole.VIEWER, UserRole.EDITOR, UserRole.ADMIN)
    return templates.TemplateResponse(
        request=request,
        name="password.html",
        context={"current_user": user, "csrf_token": csrf_token(request)},
    )


@app.post("/account/password", response_class=HTMLResponse)
def change_password(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    current_password: Annotated[str, Form()],
    new_password: Annotated[str, Form()],
    csrf: Annotated[str, Form()],
) -> Response:
    user = require_role(request, session, UserRole.VIEWER, UserRole.EDITOR, UserRole.ADMIN)
    require_csrf(request, csrf)
    if not verify_password(current_password, user.password_hash):
        return templates.TemplateResponse(
            request=request,
            name="password.html",
            context={
                "current_user": user,
                "csrf_token": csrf_token(request),
                "error": "Текущий пароль указан неверно.",
            },
            status_code=400,
        )
    try:
        user.password_hash = hash_password(new_password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    token = request.cookies.get(COOKIE_NAME)
    keep_session_id = session_id(token) if token else None
    session.execute(
        delete(AuthSession).where(
            AuthSession.user_id == user.id,
            AuthSession.id != keep_session_id,
        )
    )
    return RedirectResponse(url="/", status_code=303)


def dashboard_context(session: Session, request: Request | None = None) -> dict[str, Any]:
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
    context = {
        "latest_run": latest_run,
        "active_count": active_count or 0,
        "critical_count": critical_count or 0,
        "alert_rows": active_alert_rows,
        "historical_alert_rows": historical_alert_rows,
        "sources": list(session.scalars(select(DataSource).order_by(DataSource.name))),
        "public_api_url": get_settings().effective_public_api_url,
    }
    if request is not None:
        context["current_user"] = current_user(request, session)
        context["csrf_token"] = csrf_token(request)
    return context


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
    context = dashboard_context(session, request)
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=context,
    )


@app.post("/sources", response_class=HTMLResponse)
def create_source(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    name: Annotated[str, Form()],
    worksheet_name: Annotated[str, Form()] = "All_Data",
    csrf: Annotated[str, Form()] = "",
) -> HTMLResponse:
    require_role(request, session, UserRole.ADMIN)
    require_csrf(request, csrf)
    context = dashboard_context(session, request)
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
            context = dashboard_context(session, request)
            context["public_api_url"] = request_public_url(request)
            context["new_source"] = source
            context["new_token"] = token
        except IntegrityError:
            session.rollback()
            context = dashboard_context(session, request)
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
    csrf: Annotated[str, Form()],
) -> HTMLResponse:
    require_role(request, session, UserRole.ADMIN)
    require_csrf(request, csrf)
    source = session.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    token = generate_ingest_token()
    source.ingest_token_hash = hash_ingest_token(token)
    session.flush()
    context = dashboard_context(session, request)
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
        filtered_count = session.scalar(select(func.count(RawSnapshot.id)).where(*conditions)) or 0
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
                    (f"f{index}", filters[column])
                    for index, column in enumerate(columns)
                    if column in filters
                ]
            ),
        },
    )


@app.get("/sources/{source_id}/dashboard", response_class=HTMLResponse)
def source_performance_dashboard(
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
            .where(
                IngestionRun.source_id == source.id,
                IngestionRun.status == RunStatus.SUCCESS,
                select(RawSnapshot.id)
                .where(RawSnapshot.run_id == IngestionRun.id)
                .exists(),
            )
            .order_by(IngestionRun.finished_at.desc())
            .limit(50)
        )
    )
    user = current_user(request, session)
    return templates.TemplateResponse(
        request=request,
        name="performance_dashboard.html",
        context={
            "source": source,
            "runs": runs,
            "current_user": user,
            "can_edit": bool(user and user.role in {UserRole.EDITOR, UserRole.ADMIN}),
            "csrf_token": csrf_token(request),
        },
    )


@app.get("/sources/{source_id}/dashboard-history", response_class=HTMLResponse)
def dashboard_history(
    source_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    user = require_role(request, session, UserRole.EDITOR, UserRole.ADMIN)
    source = session.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    revisions = list(
        session.scalars(
            select(DashboardConfigRevision)
            .where(DashboardConfigRevision.source_id == source_id)
            .order_by(DashboardConfigRevision.version.desc())
        )
    )
    authors = {
        revision.changed_by: session.get(User, revision.changed_by) for revision in revisions
    }
    current = session.get(DashboardConfig, source_id)
    return templates.TemplateResponse(
        request=request,
        name="dashboard_history.html",
        context={
            "source": source,
            "revisions": revisions,
            "authors": authors,
            "current_version": current.version if current else 0,
            "current_user": user,
            "csrf_token": csrf_token(request),
        },
    )


@app.post("/sources/{source_id}/dashboard-history/{revision_id}/restore")
def restore_dashboard_revision(
    source_id: str,
    revision_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    expected_version: Annotated[int, Form()],
    csrf: Annotated[str, Form()],
) -> RedirectResponse:
    user = require_role(request, session, UserRole.EDITOR, UserRole.ADMIN)
    require_csrf(request, csrf)
    current = session.get(DashboardConfig, source_id)
    revision = session.get(DashboardConfigRevision, revision_id)
    if revision is None or revision.source_id != source_id:
        raise HTTPException(status_code=404, detail="Revision not found")
    if current is None or current.version != expected_version:
        raise HTTPException(status_code=409, detail="Dashboard was changed by another editor")
    current.version += 1
    current.config = revision.config
    current.updated_by = user.id
    session.flush()
    session.add(
        DashboardConfigRevision(
            source_id=source_id,
            version=current.version,
            config=revision.config,
            changed_by=user.id,
        )
    )
    return RedirectResponse(url=f"/sources/{source_id}/dashboard", status_code=303)


@app.get("/api/v1/sources/{source_id}/performance")
def source_performance_data(
    source_id: str,
    session: Annotated[Session, Depends(get_session)],
    run_id: str | None = None,
) -> dict[str, Any]:
    query = select(IngestionRun).where(
        IngestionRun.source_id == source_id,
        IngestionRun.status == RunStatus.SUCCESS,
        select(RawSnapshot.id).where(RawSnapshot.run_id == IngestionRun.id).exists(),
    )
    if run_id:
        query = query.where(IngestionRun.id == run_id)
    run = session.scalar(query.order_by(IngestionRun.finished_at.desc()).limit(1))
    if run is None:
        raise HTTPException(status_code=404, detail="Successful snapshot not found")
    rows = list(
        session.scalars(
            select(RawSnapshot).where(RawSnapshot.run_id == run.id).order_by(RawSnapshot.row_number)
        )
    )
    saved_config = session.get(DashboardConfig, source_id)
    return {
        "run": {
            "id": run.id,
            "snapshot_date": run.snapshot_date.isoformat(),
            "row_count": run.source_row_count,
        },
        **dashboard_payload(rows),
        "dashboard_config": saved_config.config if saved_config else None,
        "config_version": saved_config.version if saved_config else 0,
    }


@app.put("/api/v1/sources/{source_id}/dashboard-config")
def save_dashboard_config(
    source_id: str,
    payload: DashboardConfigPayload,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    editor = require_role(request, session, UserRole.EDITOR, UserRole.ADMIN)
    require_csrf(request, request.headers.get("x-csrf-token"))
    source = session.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    run = session.scalar(
        select(IngestionRun)
        .where(IngestionRun.source_id == source_id, IngestionRun.status == RunStatus.SUCCESS)
        .order_by(IngestionRun.finished_at.desc())
        .limit(1)
    )
    rows = (
        list(session.scalars(select(RawSnapshot).where(RawSnapshot.run_id == run.id)))
        if run
        else []
    )
    available = set(dashboard_payload(rows)["metrics"]) | {"ctr", "cpm", "cpc", "cpa", "roas"}
    requested = {metric for widget in payload.widgets for metric in widget.metrics}
    unknown = sorted(requested - available)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown metrics: {', '.join(unknown)}")
    ids = [widget.id for widget in payload.widgets]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail="Widget ids must be unique")
    stored = session.get(DashboardConfig, source_id)
    current_version = stored.version if stored else 0
    if payload.version != current_version:
        raise HTTPException(status_code=409, detail="Dashboard was changed by another editor")
    config = {"widgets": [widget.model_dump() for widget in payload.widgets]}
    if stored is None:
        stored = DashboardConfig(source_id=source_id, config=config, updated_by=editor.id)
        session.add(stored)
    else:
        stored.config = config
        stored.version += 1
        stored.updated_by = editor.id
    session.flush()
    session.add(
        DashboardConfigRevision(
            source_id=source_id,
            version=stored.version,
            config=config,
            changed_by=editor.id,
        )
    )
    return {"config": stored.config, "version": stored.version}


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
            select(RawSnapshot).where(*conditions).order_by(RawSnapshot.row_number).limit(500)
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
        latest_event.evidence.get("changes", []) if latest_event and latest_event.evidence else []
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
            "current_user": current_user(request, session),
            "csrf_token": csrf_token(request),
        },
    )


@app.post("/alerts/{alert_id}/resolve")
def resolve_alert(
    alert_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    reason: Annotated[str, Form()],
    resolved_by: Annotated[str, Form()] = "Manual",
    csrf: Annotated[str, Form()] = "",
) -> RedirectResponse:
    require_role(request, session, UserRole.EDITOR, UserRole.ADMIN)
    require_csrf(request, csrf)
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
