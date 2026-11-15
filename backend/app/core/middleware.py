"""Request guards: admin session, origin check and basic security headers.

The whole application is single-operator, so instead of decorating dozens of
routers the guard runs once per request:

* ``/api/auth/status|setup|login|logout`` and the readiness ping stay public;
* everything under ``/api`` and ``/media`` requires a valid signed cookie;
* static SPA files stay public (the login screen itself has to load);
* unsafe methods are only accepted from this application's own origin.

This is a **plain ASGI** middleware, not ``BaseHTTPMiddleware``. The latter
wraps every response in an anyio task group, which is measurable overhead for
the handful of JSON calls per second but a real problem for the MJPEG live view:
those responses stay open for minutes and the wrapper adds a second task layer
and its cancellation edge cases to each one.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse

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


class AdminAuthMiddleware:
    """ASGI middleware that authenticates ``/api`` and ``/media`` requests."""

    def __init__(self, app) -> None:  # noqa: ANN001 - Starlette passes the app
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        # WebSockets authenticate themselves (``api/ws.py``): SameSite cookies do
        # not cover a handshake, so the endpoint checks the session and Origin.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        path = request.url.path
        method = request.method.upper()

        # CORS preflight carries no credentials by design.
        if method != "OPTIONS":
            rejection = self._rejection(request, path, method)
            if rejection is not None:
                await rejection(scope, receive, send)
                return

        async def send_with_headers(message) -> None:  # noqa: ANN001
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)

    @staticmethod
    def _rejection(request: Request, path: str, method: str) -> JSONResponse | None:
        """A response that must be sent instead of reaching the application."""
        if not _is_protected(path) or path in PUBLIC_PATHS:
            return None
        if method in UNSAFE_METHODS and not is_allowed_origin(request):
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
        return None
