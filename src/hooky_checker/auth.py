from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from hooky_checker.db.models import AuthSession, User, UserRole

COOKIE_NAME = "hooky_session"
CSRF_COOKIE_NAME = "hooky_csrf"


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def hash_password(password: str, salt: bytes | None = None) -> str:
    if len(password) < 10:
        raise ValueError("Password must contain at least 10 characters")
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), actual_salt, 600_000)
    salt_text = base64.urlsafe_b64encode(actual_salt).decode()
    digest_text = base64.urlsafe_b64encode(digest).decode()
    return f"pbkdf2_sha256$600000${salt_text}${digest_text}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_text, expected_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(expected_text)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def session_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(request: Request) -> str:
    token = getattr(request.state, "csrf_token", None) or request.cookies.get(CSRF_COOKIE_NAME)
    if not token:
        token = secrets.token_urlsafe(32)
    request.state.csrf_token = token
    return token


def require_csrf(request: Request, supplied_token: str | None) -> None:
    expected = request.cookies.get(CSRF_COOKIE_NAME)
    if not expected or not supplied_token or not secrets.compare_digest(expected, supplied_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def current_user(request: Request, session: Session) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    auth_session = session.get(AuthSession, session_id(token))
    now = datetime.now(UTC)
    if auth_session is None:
        return None
    expires_at = auth_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        session.delete(auth_session)
        return None
    user = session.get(User, auth_session.user_id)
    return user if user and user.is_active else None


def require_role(request: Request, session: Session, *roles: UserRole) -> User:
    user = current_user(request, session)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required")
    if user.role not in roles:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return user
