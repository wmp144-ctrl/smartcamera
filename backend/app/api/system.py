"""System information, GPU telemetry, settings and maintenance endpoints."""

from __future__ import annotations

import asyncio
import logging
import platform
from dataclasses import asdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Body, HTTPException, Query  # noqa: F401
from sqlalchemy import select

from app.api.deps import SessionDep
from app.core.config import settings
from app.core.database import init_models
from app.core.timeutil import (
    JALALI_MONTHS,
    JALALI_WEEKDAYS,
    day_bounds_utc,
    jalali_label,
    to_local,
    utcnow,
)
from app.models.system import Setting
from app.schemas.system import (
    DayBoundary,
    GPUInfo,
    GPUMetric,
    ModelInfo,
    SettingRead,
    SettingUpdate,
    StorageUsage,
    SystemComponent,
    SystemInfo,
)
from app.services import environment as env_service
from app.services.camera_manager import manager
from app.services.gpu import gpu_manager
from app.services.models_registry import RFDETR_MODELS, speed_factor
from app.services.performance import (
    EDITABLE_FIELDS,
    apply_profile,
    hardware_summary,
    load_profile,
    save_profile,
    validate,
)
from app.services.retention import apply_retention
from app.services.storage import storage_usage_async

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/ping", response_model=dict)
async def ping() -> dict:
    return {"ok": True, "time": utcnow().isoformat(), "version": settings.version}


@router.get("/info", response_model=SystemInfo)
async def info(session: SessionDep) -> SystemInfo:
    now = utcnow()
    local = to_local(now, settings.timezone) or now
    usage = await storage_usage_async()
    # ``collect_components`` spawns ffmpeg and reads package metadata: it costs
    # more than the whole request it feeds, so keep it off the event loop.
    components = await asyncio.to_thread(env_service.collect_components)
    return SystemInfo(
        app_name=settings.app_name,
        version=settings.version,
        python_version=platform.python_version(),
        platform=f"{platform.system()} {platform.release()} ({platform.machine()})",
        timezone=settings.timezone,
        utc_now=now,
        local_now=local,
        jalali_now=jalali_label(local),
        database_url=_safe_database_url(settings.resolved_database_url),
        storage_root=str(settings.storage_root),
        storage_bytes=int(usage["total_bytes"]),
        components=[
            SystemComponent(name=component.name, version=component.version, available=component.available)
            for component in components
        ],
        gpu=await asyncio.to_thread(_gpu_info),
    )


@router.get("/time", response_model=dict)
async def time_info() -> dict:
    now = utcnow()
    local = to_local(now, settings.timezone) or now
    start, end = day_bounds_utc(
        local.year, local.month, local.day, settings.timezone
    )
    return {
        "utc": now.isoformat(),
        "local": local.isoformat(),
        "timezone": settings.timezone,
        "jalali": jalali_label(local),
        "jalali_long": _jalali_long(local),
        "weekday_fa": JALALI_WEEKDAYS[(local.weekday() + 2) % 7],
        "day_start_utc": start.isoformat(),
        "day_end_utc": end.isoformat(),
    }


@router.get("/day-bounds", response_model=DayBoundary)
async def day_bounds(
    year: int | None = Query(default=None, ge=1200, le=2200),
    month: int | None = Query(default=None, ge=1, le=12),
    day: int | None = Query(default=None, ge=1, le=31),
    jalali: str | None = None,
) -> DayBoundary:
    """UTC window for a Jalali or Gregorian day (the UI calendar uses this)."""
    if jalali:
        parts = jalali.replace("-", "/").split("/")
        if len(parts) != 3:
            raise HTTPException(status_code=422, detail="قالب تاریخ نامعتبر است")
        try:
            year, month, day = (int(part) for part in parts)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="قالب تاریخ نامعتبر است") from exc
    if year is None or month is None or day is None:
        local = to_local(utcnow(), settings.timezone) or utcnow()
        year, month, day = local.year, local.month, local.day
    try:
        start, end = day_bounds_utc(year, month, day, settings.timezone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="تاریخ نامعتبر است") from exc
    return DayBoundary.build(start, end)


@router.get("/gpu", response_model=GPUInfo)
async def gpu() -> GPUInfo:
    # Reading NVML (or spawning nvidia-smi) blocks; keep it off the event loop so
    # a slow query cannot add latency to live streams. Telemetry also never fails
    # the request: a broken driver reports "unavailable" instead of a 500.
    return await asyncio.to_thread(_gpu_info)


@router.get("/compatibility", response_model=dict)
async def compatibility() -> dict:
    # Imports torch and queries CUDA: hundreds of milliseconds on the event loop.
    return await asyncio.to_thread(env_service.compatibility_report)


@router.get("/models", response_model=list[ModelInfo])
async def models() -> list[ModelInfo]:
    from app.services.detector import registry as detector_registry

    loaded = {item["model_key"]: item for item in detector_registry.loaded()}
    result: list[ModelInfo] = []
    for key, spec in RFDETR_MODELS.items():
        weights = settings.models_dir / spec.weights_filename
        result.append(
            ModelInfo(
                key=key,
                name=spec.name,
                resolution=spec.resolution,
                weights_filename=spec.weights_filename,
                available=weights.exists() or key in loaded,
                path=str(weights) if weights.exists() else None,
                description=spec.description,
            )
        )
    return result


@router.get("/settings", response_model=list[SettingRead])
async def list_settings(session: SessionDep) -> list[Setting]:
    return list((await session.execute(select(Setting).order_by(Setting.key))).scalars().all())


@router.put("/settings/{key}", response_model=SettingRead)
async def upsert_setting(
    key: str, payload: SettingUpdate, session: SessionDep
) -> Setting:
    setting = await session.get(Setting, key)
    if setting is None:
        setting = Setting(key=key, value=payload.value)
        session.add(setting)
    else:
        setting.value = payload.value
    await session.commit()
    await session.refresh(setting)
    return setting


@router.get("/runtime", response_model=dict)
async def runtime_stats() -> dict:
    """In-process telemetry: queues, inference scheduler, resources, models."""
    return {
        "manager": manager.stats,
        "cameras": manager.all_health(),
        "timezone": settings.timezone,
        "detection_defaults": {
            "detection_fps": settings.default_detection_fps,
            "stream_fps": settings.default_stream_fps,
            "require_motion": settings.require_motion_for_detection,
            "stationary_after_seconds": settings.stationary_after_seconds,
        },
    }


@router.get("/performance", response_model=dict)
async def get_performance() -> dict:
    """Effective performance profile + what this machine can do.

    ``batch_size`` is reported as a *fixed* value instead of a knob: RF-DETR is
    traced for a single image at load time (``model.inference(batch_size=1)``)
    and ``predict([a, b])`` raises on that graph, so exposing a batch slider
    would only produce a setting that cannot work.
    """
    profile = load_profile()
    from app.services.inference import scheduler  # noqa: PLC0415
    from app.services.persistence import snapshot_writer  # noqa: PLC0415

    return {
        "profile": asdict(profile),
        "editable": list(EDITABLE_FIELDS),
        "hardware": hardware_summary(),
        "speed_factor": speed_factor(),
        "live": {
            "inference_queue": scheduler.stats(),
            "snapshot_writer": snapshot_writer().stats(),
            "persistence_batch_max": settings.persist_batch_max,
            "device": settings.device,
            "fp16": settings.fp16,
        },
        "fixed": {
            "batch_size": {
                "value": 1,
                "editable": False,
                "reason": (
                    "RF-DETR در زمان بارگذاری برای یک تصویر trace می‌شود؛ "
                    "batch>1 نیاز به بازسازی مدل دارد و این نسخه پشتیبانی نمی‌کند"
                ),
            }
        },
    }


@router.put("/performance", response_model=dict)
async def put_performance(payload: dict = Body(...)) -> dict:
    """Save a partial performance profile and apply it without a restart."""
    clean, errors = validate(payload or {})
    if errors:
        raise HTTPException(status_code=422, detail="؛ ".join(errors))
    if not clean:
        return {"ok": True, "changed": {}, "detail": "تغییری ارسال نشد"}

    profile = load_profile()
    previous = asdict(profile)
    for key, value in clean.items():
        setattr(profile, key, value)
    save_profile(profile)
    apply_profile(profile)
    # Global knobs (detection width, motion requirement) are copied into each
    # pipeline when it is built; without this the change only reached cameras
    # created afterwards.
    manager.apply_performance()

    # A device/precision change only takes effect for models loaded after it, so
    # every detecting camera is reloaded (the registry reloads the weights).
    reload_needed = any(key in {"fp16", "device"} for key in clean)
    if reload_needed and manager.running:
        for camera_id in list(manager.stream_ids()):
            await manager.reload_camera(camera_id)
        # The registry keeps the model of the *previous* device alive until the
        # last camera releases it; return the freed blocks to the driver so a
        # 4 GB card does not sit at "almost full" after the switch.
        await asyncio.to_thread(_release_cached_vram)

    changed = {k: v for k, v in clean.items() if previous.get(k) != v}
    logger.info("performance profile updated: %s", changed or clean)
    return {
        "ok": True,
        "changed": changed,
        "profile": asdict(profile),
        "reloaded_cameras": reload_needed,
        "detail": "تنظیمات عملکرد ذخیره و اعمال شد",
    }


@router.post("/retention/run", response_model=dict)
async def run_retention(
    session: SessionDep,
    days: int | None = Body(default=None, embed=True),
    dry_run: bool = Body(default=False, embed=True),
) -> dict:
    report = await apply_retention(session, days, dry_run=dry_run)
    return report.as_dict()


@router.post("/maintenance/init-db", response_model=dict)
async def init_db() -> dict:
    await init_models()
    return {"ok": True, "detail": "جداول ایجاد شدند"}


@router.get("/storage", response_model=StorageUsage)
async def storage() -> StorageUsage:
    return StorageUsage(**await storage_usage_async())


# --------------------------------------------------------------------------
def _release_cached_vram() -> None:
    """Best-effort ``torch.cuda.empty_cache`` for the period after a reload."""
    try:
        import torch  # noqa: PLC0415

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001 - telemetry/setup must never raise
        pass


def _safe_database_url(url: str) -> str:
    """Hide credentials before returning the URL to the UI."""
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    _, _, host = rest.rpartition("@")
    return f"{scheme}://***@{host}"


def _gpu_info() -> GPUInfo:
    try:
        snapshot = gpu_manager.snapshot(
            active_cameras=manager.active_detection_cameras(),
            detection_fps=manager.detection_fps_total(),
        )
    except Exception:  # noqa: BLE001 - telemetry is best effort
        logger.exception("GPU telemetry failed")
        return GPUInfo(available=False, source="none", devices=[])
    return GPUInfo(
        available=snapshot.available,
        source=snapshot.source,
        driver_version=snapshot.driver_version,
        cuda_version=snapshot.cuda_version,
        devices=[
            GPUMetric(
                index=device.index,
                name=device.name,
                utilization=device.utilization,
                memory_used_mb=device.memory_used_mb,
                memory_total_mb=device.memory_total_mb,
                memory_percent=device.memory_percent,
                temperature_c=device.temperature_c,
                power_w=device.power_w,
                active_cameras=device.active_cameras,
                detection_fps=device.detection_fps,
            )
            for device in snapshot.devices
        ],
    )


def _jalali_long(local: datetime) -> str:
    jy, jm, jd = (int(part) for part in jalali_label(local).split("/"))
    return f"{jd} {JALALI_MONTHS[jm - 1]} {jy}"


def _date_range_helper(start: date, days: int) -> list[date]:
    """Inclusive date range with ``days`` entries (used by report builders)."""
    return [start + timedelta(days=offset) for offset in range(days)]
