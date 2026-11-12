"""Authentication primitives: password hashing, signed cookies, URL redaction.

Only one administrator account exists, so there is no user table to manage:
the password hash and the session-signing secret live in a single row of
``admin_credentials`` (see :mod:`app.services.auth`).

Password hashing uses ``hashlib.scrypt`` (stdlib, memory-hard) so the system
keeps working on an offline machine without adding a native dependency.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from app.core.config import settings

#: Session cookie name (HttpOnly, SameSite=Lax).
SESSION_COOKIE = "camerasmart_session"

#: Shown instead of credentials embedded in a camera URL.
MASK = "***"

MIN_PASSWORD_LENGTH = 8

_TOKEN_VERSION = "v1"

# scrypt cost: ~2^14 * 8 * 1 is roughly 50-100 ms per verification on the
# class of machine this system targets, which is enough to make offline
# guessing expensive without making login feel slow.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


# --------------------------------------------------------------------------
# password hashing
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class PasswordHash:
    password_hash: str
    salt: str
    params: str


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> PasswordHash:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return PasswordHash(
        password_hash=_b64(digest),
        salt=_b64(salt),
        params=f"scrypt:{_SCRYPT_N}:{_SCRYPT_R}:{_SCRYPT_P}",
    )


def verify_password(
    password: str,
    *,
    password_hash: str,
    salt: str,
    params: str,
) -> bool:
    """Constant-time password check against a stored scrypt hash."""
    try:
        algorithm, n_raw, r_raw, p_raw = params.split(":")
        if algorithm != "scrypt":
            return False
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        expected = _unb64(password_hash)
        salt_raw = _unb64(salt)
    except (ValueError, TypeError):
        return False

    try:
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt_raw,
            n=n,
            r=r,
            p=p,
            dklen=len(expected) or _SCRYPT_DKLEN,
        )
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(digest, expected)


def validate_new_password(password: str, confirm: str | None = None) -> str | None:
    """Return a Persian error message when the password is unacceptable."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"رمز عبور باید حداقل {MIN_PASSWORD_LENGTH} نویسه باشد"
    if not password.strip():
        return "رمز عبور نمی‌تواند فقط فاصله باشد"
    if confirm is not None and password != confirm:
        return "تکرار رمز عبور یکسان نیست"
    return None


# --------------------------------------------------------------------------
# session token (HMAC-signed, expiring)
# --------------------------------------------------------------------------
class SessionSigner:
    """Signs/verifies session cookies with the secret stored in the database.

    The secret is cached in memory so the request middleware does not need a
    database round-trip on every call: a restart reloads it, and changing the
    password rotates it (which signs every existing session out).
    """

    def __init__(self) -> None:
        self._secret: bytes | None = None

    def load(self, secret: str | None) -> None:
        self._secret = secret.encode("utf-8") if secret else None

    @property
    def configured(self) -> bool:
        return self._secret is not None

    def issue(self, ttl_seconds: float) -> str:
        secret = self._secret
        if secret is None:
            raise RuntimeError("session secret is not configured")
        expires = int(time.time() + max(60.0, ttl_seconds))
        nonce = _b64(os.urandom(9))
        payload = f"{_TOKEN_VERSION}.{expires}.{nonce}"
        signature = _b64(hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest())
        return f"{payload}.{signature}"

    def verify(self, token: str | None) -> bool:
        secret = self._secret
        if secret is None or not token:
            return False
        parts = token.split(".")
        if len(parts) != 4 or parts[0] != _TOKEN_VERSION:
            return False
        _, expires_raw, nonce, signature = parts
        try:
            expires = int(expires_raw)
        except ValueError:
            return False
        if expires < int(time.time()):
            return False
        payload = f"{_TOKEN_VERSION}.{expires_raw}.{nonce}"
        expected = hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest()
        try:
            provided = _unb64(signature)
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(provided, expected)


#: Process-wide signer used by the middleware and the WebSocket endpoint.
signer = SessionSigner()


def session_ttl_seconds() -> int:
    return int(max(1.0, float(settings.session_hours)) * 3600)


def current_session_token() -> str:
    """Mint a cookie value for the current secret (used by login endpoints)."""
    return signer.issue(session_ttl_seconds())


# --------------------------------------------------------------------------
# request origin / CSRF helper
# --------------------------------------------------------------------------
def is_allowed_origin(request) -> bool:
    """True when an unsafe request comes from this app's own origin.

    Cookies are ``SameSite=Lax``, which already blocks cross-site POST/PATCH,
    but an explicit Origin check is cheap and catches older browsers plus
    WebSocket handshakes (which are not covered by SameSite).
    """
    origin = request.headers.get("origin")
    if not origin:
        # Same-origin fetches from some clients omit Origin; nothing to verify.
        return True
    parsed = urlsplit(origin)
    host = (request.headers.get("host") or "").lower()
    if host and parsed.netloc.lower() == host:
        return True
    allowed = {item.rstrip("/").lower() for item in settings.cors_origins if item}
    return origin.rstrip("/").lower() in allowed


# --------------------------------------------------------------------------
# camera URL redaction
# --------------------------------------------------------------------------
def has_url_credentials(url: str | None) -> bool:
    if not url:
        return False
    try:
        return "@" in urlsplit(url).netloc
    except ValueError:
        return False


def mask_url_credentials(url: str | None) -> str:
    """``rtsp://user:pass@host/path`` -> ``rtsp://***@host/path``."""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if "@" not in parts.netloc:
        return url
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, f"{MASK}@{host}", parts.path, parts.query, parts.fragment))


def is_masked_url(url: str | None) -> bool:
    """True when the client sent back the redacted URL unchanged."""
    return bool(url) and f"{MASK}@" in url  # type: ignore[operator]


def new_session_secret() -> str:
    return secrets.token_urlsafe(48)
