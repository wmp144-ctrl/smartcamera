"""GPU telemetry tests.

NVML is not trustworthy in every process: on this host a fresh interpreter reads
utilisation fine, but the *server* process (torch has created a CUDA context by
then) gets ``NVMLError_Unknown``. That used to bubble out of ``/api/system/gpu``
as a 500 on every poll, so the contract pinned here is that telemetry degrades to
``nvidia-smi`` (or to "unavailable") and never raises.
"""

from __future__ import annotations

import pytest

from app.services.gpu import DeviceSnapshot, GPUManager, GPUSnapshot


class _Memory:
    def __init__(self, total: int, used: int) -> None:
        self.total = total
        self.used = used


class _Utilization:
    def __init__(self, gpu: int) -> None:
        self.gpu = gpu
        self.memory = 0


class FakeNVML:
    """Minimal ``pynvml`` stand-in; ``util_fails`` reproduces the Windows fault."""

    NVML_TEMPERATURE_GPU = 0

    def __init__(self, *, util_fails: bool = False) -> None:
        self.util_fails = util_fails

    def nvmlSystemGetDriverVersion(self) -> bytes:
        return b"592.27"

    def nvmlSystemGetCudaDriverVersion_v2(self) -> int:
        return 13010

    def nvmlDeviceGetCount(self) -> int:
        return 1

    def nvmlDeviceGetHandleByIndex(self, index: int) -> int:
        return index

    def nvmlDeviceGetName(self, handle: int) -> str:
        return "Fake GPU"

    def nvmlDeviceGetMemoryInfo(self, handle: int) -> _Memory:
        return _Memory(total=4096 * 1024 * 1024, used=512 * 1024 * 1024)

    def nvmlDeviceGetUtilizationRates(self, handle: int) -> _Utilization:
        if self.util_fails:
            raise RuntimeError("NVMLError_Unknown")
        return _Utilization(gpu=37)

    def nvmlDeviceGetTemperature(self, handle: int, sensor: int) -> int:
        return 55

    def nvmlDeviceGetPowerUsage(self, handle: int) -> int:
        return 42000


def _manager(nvml: object | None) -> GPUManager:
    manager = GPUManager()
    manager._nvml = nvml
    manager._nvml_checked = True
    return manager


def test_healthy_nvml_is_used(monkeypatch) -> None:
    manager = _manager(FakeNVML())
    monkeypatch.setattr(
        manager, "_from_smi", lambda: pytest.fail("nvidia-smi must not be used")
    )

    snapshot = manager.snapshot(active_cameras=3, detection_fps=9.5)

    assert snapshot.source == "pynvml"
    assert snapshot.driver_version == "592.27"
    assert snapshot.cuda_version == "13.1"
    device = snapshot.devices[0]
    assert device.name == "Fake GPU"
    assert device.utilization == 37.0
    assert device.memory_total_mb == 4096.0
    assert device.temperature_c == 55.0
    assert device.power_w == 42.0
    assert device.active_cameras == 3
    assert device.detection_fps == 9.5


def test_nvml_utilization_error_falls_back_to_nvidia_smi(monkeypatch) -> None:
    """The exact failure seen in production: one bad NVML query, whole endpoint 500s."""
    manager = _manager(FakeNVML(util_fails=True))
    fallback = GPUSnapshot(
        available=True,
        source="nvidia-smi",
        driver_version="592.27",
        cuda_version="13.1",
        devices=[DeviceSnapshot(index=0, name="SMI GPU", utilization=42.0, memory_total_mb=4096.0)],
    )
    monkeypatch.setattr(manager, "_from_smi", lambda: fallback)

    snapshot = manager.snapshot(active_cameras=1, detection_fps=4.0)

    assert snapshot.source == "nvidia-smi"
    assert snapshot.devices[0].utilization == 42.0
    assert snapshot.devices[0].active_cameras == 1
    assert snapshot.devices[0].detection_fps == 4.0


def test_every_provider_failing_reports_unavailable(monkeypatch) -> None:
    manager = _manager(None)
    monkeypatch.setattr(manager, "_from_smi", lambda: GPUSnapshot(available=False, source="none"))

    snapshot = manager.snapshot()

    assert snapshot.available is False
    assert snapshot.devices == []


def test_nvidia_smi_fallback_is_cached_longer_than_nvml(monkeypatch) -> None:
    """Spawning nvidia-smi is expensive, so it must not run on every poll."""
    calls: list[int] = []

    def fake_smi() -> GPUSnapshot:
        calls.append(1)
        return GPUSnapshot(
            available=True,
            source="nvidia-smi",
            devices=[DeviceSnapshot(index=0, name="SMI GPU", memory_total_mb=4096.0)],
        )

    manager = GPUManager(cache_ttl=0.0)
    manager._nvml = None
    manager._nvml_checked = True
    monkeypatch.setattr(manager, "_from_smi", fake_smi)

    manager.snapshot()
    manager.snapshot()

    assert len(calls) == 1


async def test_gpu_endpoint_survives_a_broken_driver(client, monkeypatch) -> None:
    from app.api import system as system_api

    def explode(**kwargs: object) -> GPUSnapshot:
        raise RuntimeError("NVML exploded")

    monkeypatch.setattr(type(system_api.gpu_manager), "snapshot", explode)

    response = await client.get("/api/system/gpu")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["devices"] == []


async def test_system_info_survives_a_broken_driver(client, monkeypatch) -> None:
    from app.api import system as system_api

    def explode(**kwargs: object) -> GPUSnapshot:
        raise RuntimeError("NVML exploded")

    monkeypatch.setattr(type(system_api.gpu_manager), "snapshot", explode)

    response = await client.get("/api/system/info")

    assert response.status_code == 200
    assert response.json()["gpu"]["available"] is False
