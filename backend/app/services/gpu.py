"""GPU manager: VRAM, utilisation, temperature, plus detector load.

Prefers ``pynvml`` (cheap, in-process) and falls back to parsing
``nvidia-smi --query-gpu`` so the dashboard still shows real numbers on a
machine where the NVML Python bindings are missing.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field

logger = logging.getLogger(__name__)

_CACHE_TTL = 2.0
# Spawning nvidia-smi costs a few hundred milliseconds, so cache its answer for
# longer than an in-process NVML read.
_SMI_CACHE_TTL = 5.0


class _NVMLUnavailable(RuntimeError):
    """A required NVML query failed; the caller should fall back to nvidia-smi."""


def _optional(func):  # noqa: ANN001, ANN201
    """Return ``func()`` or ``None`` when the NVML call raises.

    NVML is not reliable in every process: a query that succeeds in a fresh
    interpreter can answer ``NVMLError_Unknown`` once torch has created a CUDA
    context. Telemetry must degrade, never take the API down.
    """
    try:
        return func()
    except Exception as exc:  # noqa: BLE001
        logger.debug("optional NVML query failed: %s", exc)
        return None


@dataclass
class DeviceSnapshot:
    index: int = 0
    name: str = "unknown"
    utilization: float = 0.0
    memory_used_mb: float = 0.0
    memory_total_mb: float = 0.0
    memory_percent: float = 0.0
    temperature_c: float | None = None
    power_w: float | None = None
    active_cameras: int = 0
    detection_fps: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class GPUSnapshot:
    available: bool = False
    source: str = "none"
    driver_version: str | None = None
    cuda_version: str | None = None
    devices: list[DeviceSnapshot] = field(default_factory=list)


class GPUManager:
    def __init__(self, *, cache_ttl: float = _CACHE_TTL) -> None:
        self.cache_ttl = cache_ttl
        self._lock = threading.Lock()
        self._cached: GPUSnapshot | None = None
        self._cached_at = 0.0
        self._nvml: object | None = None
        self._nvml_checked = False
        self._nvml_warned = False

    # ------------------------------------------------------------------
    def _load_nvml(self) -> object | None:
        if self._nvml_checked:
            return self._nvml
        self._nvml_checked = True
        try:  # pragma: no cover - depends on the host
            import pynvml  # noqa: PLC0415

            pynvml.nvmlInit()
            self._nvml = pynvml
        except Exception as exc:  # noqa: BLE001
            logger.debug("pynvml unavailable: %s", exc)
            self._nvml = None
        return self._nvml

    def _from_nvml(self, pynvml) -> GPUSnapshot:  # noqa: ANN001
        """Read every device through NVML.

        Raises :class:`_NVMLUnavailable` when a metric the dashboard depends on
        (memory or utilisation) cannot be read, so :meth:`snapshot` can switch to
        nvidia-smi instead of reporting a misleading zero.
        """
        driver = _optional(pynvml.nvmlSystemGetDriverVersion)
        if isinstance(driver, bytes):
            driver = driver.decode()
        cuda_raw = _optional(pynvml.nvmlSystemGetCudaDriverVersion_v2)
        cuda = None
        if cuda_raw is not None:
            try:
                major, minor = divmod(int(cuda_raw), 1000)
                cuda = f"{major}.{minor // 10}"
            except (TypeError, ValueError):
                cuda = None

        count = pynvml.nvmlDeviceGetCount()
        devices: list[DeviceSnapshot] = []
        degraded: list[int] = []
        for index in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            name = _optional(lambda h=handle: pynvml.nvmlDeviceGetName(h)) or "unknown"
            if isinstance(name, bytes):
                name = name.decode()
            memory = _optional(lambda h=handle: pynvml.nvmlDeviceGetMemoryInfo(h))
            utilization = _optional(lambda h=handle: pynvml.nvmlDeviceGetUtilizationRates(h))
            if memory is None or utilization is None:
                degraded.append(index)
                continue
            temperature = _optional(
                lambda h=handle: float(
                    pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
                )
            )
            power = _optional(lambda h=handle: float(pynvml.nvmlDeviceGetPowerUsage(h)) / 1000.0)
            total = float(memory.total) / (1024 * 1024)
            used = float(memory.used) / (1024 * 1024)
            devices.append(
                DeviceSnapshot(
                    index=index,
                    name=str(name),
                    utilization=float(utilization.gpu),
                    memory_used_mb=round(used, 1),
                    memory_total_mb=round(total, 1),
                    memory_percent=round(used / total * 100.0, 1) if total else 0.0,
                    temperature_c=temperature,
                    power_w=round(power, 1) if power is not None else None,
                )
            )
        if degraded:
            raise _NVMLUnavailable(f"NVML returned no usable data for device(s) {degraded}")
        return GPUSnapshot(
            available=True,
            source="pynvml",
            driver_version=str(driver) if driver else None,
            cuda_version=cuda,
            devices=devices,
        )

    def _from_smi(self) -> GPUSnapshot:
        exe = shutil.which("nvidia-smi")
        if not exe:
            return GPUSnapshot(available=False, source="none")
        query = (
            "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw"
        )
        try:
            completed = subprocess.run(  # noqa: S603
                [exe, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("nvidia-smi failed: %s", exc)
            return GPUSnapshot(available=False, source="none")
        if completed.returncode != 0:
            return GPUSnapshot(available=False, source="none")

        devices: list[DeviceSnapshot] = []
        for line in completed.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            try:
                index = int(parts[0])
                total = float(parts[4])
                used = float(parts[3])
                temperature = _safe_float(parts[5]) if len(parts) > 5 else None
                power = _safe_float(parts[6]) if len(parts) > 6 else None
            except ValueError:
                continue
            devices.append(
                DeviceSnapshot(
                    index=index,
                    name=parts[1],
                    utilization=_safe_float(parts[2]) or 0.0,
                    memory_used_mb=used,
                    memory_total_mb=total,
                    memory_percent=round(used / total * 100, 1) if total else 0.0,
                    temperature_c=temperature,
                    power_w=power,
                )
            )

        driver = cuda = None
        try:
            header = subprocess.run(  # noqa: S603
                [exe], capture_output=True, text=True, timeout=8, check=False
            ).stdout
            for line in header.splitlines():
                if "Driver Version" in line:
                    driver = line.split("Driver Version:")[1].split()[0]
                if "CUDA Version" in line:
                    cuda = line.split("CUDA Version:")[1].split()[0]
        except Exception:  # noqa: BLE001
            pass

        return GPUSnapshot(
            available=bool(devices),
            source="nvidia-smi",
            driver_version=driver,
            cuda_version=cuda,
            devices=devices,
        )

    # ------------------------------------------------------------------
    def snapshot(
        self,
        *,
        active_cameras: int = 0,
        detection_fps: float = 0.0,
        force: bool = False,
    ) -> GPUSnapshot:
        now = time.monotonic()
        with self._lock:
            if self._cached is not None:
                ttl = _SMI_CACHE_TTL if self._cached.source == "nvidia-smi" else self.cache_ttl
                if not force and now - self._cached_at < ttl:
                    result = self._cached
                else:
                    result = self._read(now)
            else:
                result = self._read(now)

        # decorate with detector load (not part of the cached hardware data)
        for device in result.devices:
            device.active_cameras = active_cameras
            device.detection_fps = round(detection_fps, 2)
        return result

    def _read(self, now: float) -> GPUSnapshot:
        """Read hardware telemetry, caching the result. Never raises."""
        result: GPUSnapshot | None = None
        pynvml = self._load_nvml()
        if pynvml is not None:
            try:
                candidate = self._from_nvml(pynvml)
                result = candidate if candidate.devices else None
            except Exception as exc:  # noqa: BLE001 - telemetry must never break the API
                if not self._nvml_warned:
                    self._nvml_warned = True
                    logger.warning(
                        "NVML telemetry unusable in this process (%s); using nvidia-smi",
                        exc,
                    )
        if result is None:
            result = self._from_smi()
        self._cached = result
        self._cached_at = now
        return result


def _safe_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


gpu_manager = GPUManager()
