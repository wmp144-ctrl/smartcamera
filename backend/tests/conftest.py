"""Shared pytest fixtures.

The environment is redirected to a throwaway data directory *before* the
application package is imported, because ``app.core.config`` binds the settings
at import time.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_TEST_DIR = Path(tempfile.mkdtemp(prefix="camerasmart-tests-"))
os.environ["CAMERASMART_DATA_DIR"] = str(_TEST_DIR)
os.environ["CAMERASMART_DATABASE_URL"] = f"sqlite+aiosqlite:///{(_TEST_DIR / 'test.db').as_posix()}"
os.environ["CAMERASMART_TIMEZONE"] = "Asia/Tehran"
os.environ["CAMERASMART_ALERTS_ENABLED"] = "true"
# Never spawn ffmpeg/RTSP worker threads during the test suite; the streaming
# layer is exercised separately with an injected fake reader.
os.environ["CAMERASMART_AUTOSTART_CAMERAS"] = "false"

import numpy as np  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core import database  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models import Base  # noqa: E402


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001, ARG001
    shutil.rmtree(_TEST_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def clean_shared_state():
    """The camera manager and the event bus are process-wide singletons.

    Tests must not leak streams, track maps, cooldowns or history into each
    other, so the shared state is reset around every single test.
    """
    from app.services.bus import bus
    from app.services.camera_manager import manager

    manager.reset()
    bus.clear_history()
    yield
    manager.reset()
    bus.clear_history()


@pytest_asyncio.fixture
async def prepared_db():
    """Create the schema once per test (fast: SQLite file + NullPool)."""
    async with database.async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with database.async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def session(prepared_db):  # noqa: ARG001
    async with database.async_session_factory() as db:
        yield db
        await db.rollback()


@pytest_asyncio.fixture
async def client(prepared_db):  # noqa: ARG001
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest.fixture
def frame_factory():
    """Build deterministic synthetic BGR frames."""

    def _make(width: int = 320, height: int = 180, value: int = 30) -> np.ndarray:
        return np.full((height, width, 3), value, dtype=np.uint8)

    return _make


@pytest.fixture
def data_dir() -> Path:
    return _TEST_DIR
