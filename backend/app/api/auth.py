"""Single-admin authentication endpoints.

Flow:
* first launch -> ``GET /api/auth/status`` reports ``configured: false`` and the
  UI asks for the password twice, then calls ``POST /api/auth/setup``;
* afterwards -> ``POST /api/auth/login`` with the one admin password;
* changing the password rotates the session secret, signing out other clients.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.deps import SessionDep
from app.core.config import settings
from app.core.security import (
    SESSION_COOKIE,
    current_session_token,
    session_ttl_seconds,
    validate_new_password,
)
from app.services import auth as auth_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


class PasswordPayload(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class SetupPayload(PasswordPayload):
    confirm: str = Field(min_length=1, max_length=256)


class ChangePasswordPayload(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)
    confirm: str = Field(min_length=1, max_length=256)


def _client_key(request: Request) -> str:
    return request.client.host if request.client and request.client.host else "unknown"


def _attach_session_cookie(request: Request, response: Response) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        current_session_token(),
        max_age=session_ttl_seconds(),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


def _status_payload(configured: bool, authenticated: bool, detail: str = "") -> dict:
    return {
        "configured": configured,
        "authenticated": authenticated,
        "detail": detail,
        "session_hours": round(float(settings.session_hours), 2),
    }


@router.get("/status", response_model=dict)
async def auth_status(request: Request, session: SessionDep) -> dict:
    configured = await auth_service.is_configured(session)
    authenticated = configured and auth_service.signer.verify(
        request.cookies.get(SESSION_COOKIE)
    )
    return _status_payload(configured, authenticated)


@router.post("/setup", response_model=dict)
async def setup(
    payload: SetupPayload, request: Request, response: Response, session: SessionDep
) -> dict:
    """Create the admin password; only possible while none is configured."""
    if await auth_service.is_configured(session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="رمز ادمین قبلاً تنظیم شده است؛ از صفحه ورود استفاده کنید",
        )
    error = validate_new_password(payload.password, payload.confirm)
    if error:
        raise HTTPException(status_code=422, detail=error)

    await auth_service.set_password(session, payload.password)
    _attach_session_cookie(request, response)
    logger.info("admin password configured")
    return _status_payload(True, True, "رمز ادمین تنظیم شد")


@router.post("/login", response_model=dict)
async def login(
    payload: PasswordPayload, request: Request, response: Response, session: SessionDep
) -> dict:
    if not await auth_service.is_configured(session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="هنوز رمزی تنظیم نشده است؛ ابتدا رمز ادمین را تعیین کنید",
        )

    key = _client_key(request)
    wait = auth_service.throttle.retry_after(key)
    if wait > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"تلاش‌های ناموفق زیاد بود؛ {int(wait) + 1} ثانیه صبر کنید",
            headers={"Retry-After": str(int(wait) + 1)},
        )

    if not await auth_service.check_password(session, payload.password):
        auth_service.throttle.register_failure(key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="رمز عبور نادرست است",
        )

    auth_service.throttle.reset(key)
    _attach_session_cookie(request, response)
    return _status_payload(True, True, "خوش آمدید")


@router.post("/logout", response_model=dict)
async def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True, "detail": "از حساب خارج شدید"}


@router.post("/password", response_model=dict)
async def change_password(
    payload: ChangePasswordPayload,
    request: Request,
    response: Response,
    session: SessionDep,
) -> dict:
    """Change the admin password (requires the current one)."""
    if not await auth_service.check_password(session, payload.current_password):
        raise HTTPException(status_code=401, detail="رمز فعلی نادرست است")
    error = validate_new_password(payload.new_password, payload.confirm)
    if error:
        raise HTTPException(status_code=422, detail=error)

    await auth_service.set_password(session, payload.new_password)
    _attach_session_cookie(request, response)
    logger.info("admin password changed")
    return _status_payload(True, True, "رمز عبور تغییر کرد")


__all__ = ["router"]
