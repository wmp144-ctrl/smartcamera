"""Admin credential storage and login throttling.

There is exactly one admin account. The first launch has no credential, which
is what the UI uses to offer the "set the password twice" setup screen; after
that every session requires the password.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    PasswordHash,
    hash_password,
    new_session_secret,
    signer,
    verify_password,
)
from app.models.system import AdminCredential

logger = logging.getLogger(__name__)

#: The single admin row always uses this primary key.
ADMIN_ID = 1


async def get_credential(session: AsyncSession) -> AdminCredential | None:
    return await session.get(AdminCredential, ADMIN_ID)


async def is_configured(session: AsyncSession) -> bool:
    return await get_credential(session) is not None


async def load_signer(session: AsyncSession) -> bool:
    """Load the signing secret into the in-process signer (called at startup)."""
    credential = await get_credential(session)
    signer.load(credential.session_secret if credential is not None else None)
    return credential is not None


async def set_password(session: AsyncSession, password: str) -> AdminCredential:
    """Create or replace the admin password and rotate the session secret.

    Rotating the secret is deliberate: changing the password invalidates every
    session that was issued with the old one.
    """
    hashed: PasswordHash = hash_password(password)
    secret = new_session_secret()
    credential = await get_credential(session)
    if credential is None:
        credential = AdminCredential(
            id=ADMIN_ID,
            password_hash=hashed.password_hash,
            salt=hashed.salt,
            params=hashed.params,
            session_secret=secret,
        )
        session.add(credential)
    else:
        credential.password_hash = hashed.password_hash
        credential.salt = hashed.salt
        credential.params = hashed.params
        credential.session_secret = secret
        from app.core.timeutil import utcnow  # noqa: PLC0415 - avoids a cycle at import time

        credential.password_changed_at = utcnow()

    await session.commit()
    await session.refresh(credential)
    signer.load(secret)
    return credential


async def check_password(session: AsyncSession, password: str) -> bool:
    credential = await get_credential(session)
    if credential is None:
        return False
    return verify_password(
        password,
        password_hash=credential.password_hash,
        salt=credential.salt,
        params=credential.params,
    )


class LoginThrottle:
    """Small in-memory brute-force guard (single process, single admin)."""

    def __init__(self, *, max_attempts: int = 5, lockout_seconds: float = 60.0) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.lockout_seconds = max(1.0, float(lockout_seconds))
        self._failures: dict[str, int] = defaultdict(int)
        self._locked_until: dict[str, float] = {}

    def retry_after(self, key: str) -> float:
        """Seconds the caller must still wait (0 = allowed right now)."""
        until = self._locked_until.get(key)
        if until is None:
            return 0.0
        remaining = until - time.monotonic()
        if remaining <= 0:
            self._locked_until.pop(key, None)
            self._failures.pop(key, None)
            return 0.0
        return remaining

    def register_failure(self, key: str) -> None:
        failures = self._failures.get(key, 0) + 1
        self._failures[key] = failures
        if failures >= self.max_attempts:
            self._locked_until[key] = time.monotonic() + self.lockout_seconds
            self._failures[key] = 0

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)


throttle = LoginThrottle()
