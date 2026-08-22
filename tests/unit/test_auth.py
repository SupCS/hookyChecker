from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from hooky_checker.api.app import DashboardConfigPayload, WidgetConfig, save_dashboard_config
from hooky_checker.auth import (
    COOKIE_NAME,
    CSRF_COOKIE_NAME,
    current_user,
    hash_password,
    require_csrf,
    session_id,
    verify_password,
)
from hooky_checker.db.models import (
    AuthSession,
    Base,
    DashboardConfig,
    DashboardConfigRevision,
    DataSource,
    User,
    UserRole,
)
from hooky_checker.pipeline.snapshot import publish_push_snapshot


def cookie_request(token: str | None = None, csrf: str = "test-csrf") -> Request:
    cookies = [f"{CSRF_COOKIE_NAME}={csrf}"]
    if token is not None:
        cookies.append(f"{COOKIE_NAME}={token}")
    headers = [(b"cookie", "; ".join(cookies).encode()), (b"x-csrf-token", csrf.encode())]
    return Request({"type": "http", "method": "PUT", "path": "/", "headers": headers})


def test_password_hash_is_salted_and_verifiable() -> None:
    first = hash_password("a sufficiently long password")
    second = hash_password("a sufficiently long password")
    assert first != second
    assert verify_password("a sufficiently long password", first)
    assert not verify_password("wrong password", first)


def test_csrf_requires_matching_cookie_and_submitted_token() -> None:
    request = cookie_request()
    require_csrf(request, "test-csrf")
    with pytest.raises(HTTPException) as exc:
        require_csrf(request, "wrong")
    assert exc.value.status_code == 403


def test_anonymous_is_viewer_but_cannot_save_dashboard_config() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        source = DataSource(name="Public", worksheet_name="Data")
        session.add(source)
        session.flush()
        payload = DashboardConfigPayload(
            version=0,
            widgets=[WidgetConfig(id="cost", type="kpi", title="Cost", metrics=["cost"])],
        )
        with pytest.raises(HTTPException) as exc:
            save_dashboard_config(source.id, payload, cookie_request(), session)
        assert exc.value.status_code == 401


def test_editor_can_save_valid_dashboard_config() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        source = DataSource(name="Editable", worksheet_name="Data")
        editor = User(
            email="editor@example.com",
            display_name="Editor",
            password_hash=hash_password("editor password 123"),
            role=UserRole.EDITOR,
        )
        session.add_all([source, editor])
        session.flush()
        publish_push_snapshot(
            session,
            source.id,
            [["Date", "Cost", "Lead Form"], ["2026-08-01", 100, 4]],
            date(2026, 8, 22),
        )
        token = "test-session-token"
        session.add(
            AuthSession(
                id=session_id(token),
                user_id=editor.id,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        session.flush()

        result = save_dashboard_config(
            source.id,
            DashboardConfigPayload(
                version=0,
                widgets=[
                    WidgetConfig(
                        id="leads",
                        type="table",
                        title="Leads by channel",
                        metrics=["Lead Form"],
                        breakdown="channel",
                        formats={"Lead Form": "number"},
                    )
                ],
            ),
            cookie_request(token),
            session,
        )

        assert result["version"] == 1
        assert session.get(DashboardConfig, source.id).updated_by == editor.id
        revision = session.query(DashboardConfigRevision).one()
        assert revision.changed_by == editor.id
        assert result["config"]["widgets"][0]["formats"] == {"Lead Form": "number"}
        assert current_user(cookie_request(token), session) == editor
