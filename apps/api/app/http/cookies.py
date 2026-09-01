"""Secure authentication Cookie policies."""

from datetime import UTC, datetime

from backend_core.auth.service import LoginResult, OperatorAuthenticationResult
from backend_core.config import Settings
from fastapi import Response


def set_auth_cookies(
    response: Response,
    result: LoginResult | OperatorAuthenticationResult,
    settings: Settings,
) -> None:
    max_age = max(
        0,
        int((result.auth_session.expires_at.astimezone(UTC) - datetime.now(UTC)).total_seconds()),
    )
    response.set_cookie(
        key=settings.session_cookie_name,
        value=result.session_token,
        max_age=max_age,
        expires=max_age,
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=result.csrf_token,
        max_age=max_age,
        expires=max_age,
        path="/",
        secure=settings.secure_cookies,
        httponly=False,
        samesite="lax",
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        settings.csrf_cookie_name,
        path="/",
        secure=settings.secure_cookies,
        httponly=False,
        samesite="lax",
    )
