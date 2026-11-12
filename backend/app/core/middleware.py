"""Request guards: admin session, origin check and basic security headers.

The whole application is single-operator, so instead of decorating dozens of
routers the guard runs once per request:

* ``/api/auth/status|setup|login|logout`` and the readiness ping stay public;
* everything under ``/api`` and ``/media`` requires a valid signed cookie;
* static SPA files stay public (the login screen itself has to load);
* unsafe methods are only accepted from this application's own origin.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.security import SESSION_COOKIE, is_allowed_origin, signer

#: Exact paths that never require a session.
PUBLIC_PATHS = frozenset(
    {
        "/api/system/ping",
        "/api/auth/status",
        "/api/auth/setup",
        "/api/auth/login",
        "/api/auth/logout",
    }
)

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "same-origin",
}


def _is_protected(path: str) -> bool:
    return path.startswith("/api/") or path == "/media" or path.startswith("/media/")


class AdminAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # CORS preflight carries no credentials by design.
        if request.method == "OPTIONS":
            return await call_next(request)

        response: Response
        if _is_protected(path) and path not in PUBLIC_PATHS:
            if request.method in UNSAFE_METHODS and not is_allowed_origin(request):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "منبع درخواست مجاز نیست"},
                    headers=SECURITY_HEADERS,
                )
            if not signer.verify(request.cookies.get(SESSION_COOKIE)):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "برای دسترسی باید با رمز ادمین وارد شوید"},
                    headers={"WWW-Authenticate": "Cookie", **SECURITY_HEADERS},
                )

        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response
