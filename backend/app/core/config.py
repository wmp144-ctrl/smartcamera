"""Application configuration.

All settings are environment-overridable with the ``CAMERASMART_`` prefix,
e.g. ``CAMERASMART_TIMEZONE=Asia/Tehran``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CAMERASMART_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- identity / runtime -------------------------------------------------
    app_name: str = "CameraSmart"
    version: str = "1.0.0"
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 8000

    # --- timezone -----------------------------------------------------------
    # Everything is persisted in UTC; this is only the *display* timezone the
    # UI uses to render local time and Jalali dates.
    timezone: str = "Asia/Tehran"

    # --- storage ------------------------------------------------------------
    data_dir: Path = Field(default=BACKEND_ROOT / "data")

    # --- database -----------------------------------------------------------
    # SQLite by default so the app runs fully offline with zero extra services.
    # PostgreSQL is supported by pointing this at an asyncpg URL.
    database_url: str = ""
    # Log every SQL statement (very chatty; only for diagnosing queries).
    sql_echo: bool = False

    # --- detection ----------------------------------------------------------
    default_model: str = "rf-detr-base"  # RF-DETR weight key
    default_detection_fps: float = 5.0
    default_stream_fps: int = 20
    # Minimum fraction of a frame that must change for "motion" to register.
    motion_sensitivity: float = 0.004
    # An object is considered stationary when it does not move for this long.
    stationary_after_seconds: float = 8.0
    # Only capture a new "best" image while a track stays in frame if the
    # quality score improves this much (avoids image floods for static objects).
    best_image_improvement: float = 0.08
    # Corroborating detections before an object is recorded (reduces flicker).
    min_track_hits: int = 2
    # How long a track survives without a match before an "exit" event fires.
    track_max_age_seconds: float = 2.0
    # Run detection only when motion was seen in a process region.
    require_motion_for_detection: bool = True
    # GPU device for RF-DETR: "cuda", "cuda:0" or "cpu".
    device: str = "cuda"
    jpeg_quality: int = 80
    # Live-view JPEGs are downscaled to this width to keep many cameras cheap.
    live_max_width: int = 1280
    # Path to ffmpeg/ffprobe; "ffmpeg" resolves from PATH.
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    # Bounded queue between the video threads and the persistence task.
    result_queue_size: int = 48
    # Create tables on startup when they are missing (handy for SQLite/first
    # run; production deployments can rely on Alembic and set this to false).
    auto_create_tables: bool = True
    # Start camera streams automatically with the API.
    autostart_cameras: bool = True

    # --- storage ------------------------------------------------------------
    # "local" groups folders by the display timezone (operator friendly),
    # "utc" groups them by UTC (matches the database exactly).
    storage_date_mode: str = "local"
    retention_days: int = 30
    # Delete old images/events/health rows on a timer so an unattended machine
    # can never fill its disk (the manual endpoint stays available as well).
    retention_auto_run: bool = True
    retention_interval_hours: float = 24.0
    heatmap_grid_w: int = 64
    heatmap_grid_h: int = 36

    # --- authentication -------------------------------------------------------
    #: How long a browser session stays valid after login.
    session_hours: float = 12.0
    #: Failed password attempts per client before a temporary lockout.
    auth_max_attempts: int = 5
    auth_lockout_seconds: float = 60.0

    # --- alerts -------------------------------------------------------------
    alerts_enabled: bool = True
    alert_cooldown_seconds: float = 30.0
    alert_sound_default: bool = True

    # --- CORS ---------------------------------------------------------------
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
        ]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    # --- derived paths ------------------------------------------------------
    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        db_path = (self.data_dir / "camerasmart.db").as_posix()
        return f"sqlite+aiosqlite:///{db_path}"

    @property
    def storage_root(self) -> Path:
        return self.data_dir / "storage"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def frontend_dist(self) -> Path | None:
        """The built frontend, when present.

        Serving it from the API process means a deployment is a single command:
        no second web server, which matters for offline kiosk machines.
        """
        candidate = BACKEND_ROOT.parent / "frontend" / "dist"
        if (candidate / "index.html").is_file():
            return candidate
        return None

    @property
    def snapshots_dir(self) -> Path:
        return self.storage_root / "objects"

    @property
    def ensure_dirs(self) -> Path:
        for path in (self.data_dir, self.storage_root, self.snapshots_dir, self.models_dir):
            path.mkdir(parents=True, exist_ok=True)
        return self.data_dir


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """Clear the cache (used by tests that change environment variables)."""
    get_settings.cache_clear()
    return get_settings()


settings = get_settings()

# Make sure ``data/`` exists as early as possible.
if os.environ.get("CAMERASMART_SKIP_MKDIR") != "1":
    settings.ensure_dirs
