"""Runtime environment / version compatibility reporting.

Surfaced in the UI (and in bug reports) so a mismatch between PyTorch,
TorchVision, CUDA and ONNX Runtime is immediately visible instead of showing up
later as a cryptic inference error.
"""

from __future__ import annotations

import importlib.metadata as metadata
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

PYTHON_KEY = "python"

# package name -> human label
COMPONENTS: dict[str, str] = {
    "fastapi": "FastAPI",
    "uvicorn": "Uvicorn",
    "starlette": "Starlette",
    "pydantic": "Pydantic",
    "sqlalchemy": "SQLAlchemy",
    "alembic": "Alembic",
    "aiosqlite": "aiosqlite",
    "asyncpg": "asyncpg",
    "numpy": "NumPy",
    "pillow": "Pillow",
    "torch": "PyTorch",
    "torchvision": "TorchVision",
    "onnxruntime-gpu": "ONNX Runtime (GPU)",
    "onnxruntime": "ONNX Runtime",
    "opencv-python": "OpenCV",
    "rfdetr": "RF-DETR",
    "supervision": "Supervision",
    "pynvml": "NVML (pynvml)",
    "scipy": "SciPy",
    "transformers": "Transformers",
}


@dataclass
class Component:
    name: str
    version: str
    available: bool
    extra: dict = field(default_factory=dict)


def _version(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001
        return None


def ffmpeg_version(binary: str = "ffmpeg") -> str | None:
    exe = shutil.which(binary)
    if not exe:
        return None
    try:
        completed = subprocess.run(  # noqa: S603
            [exe, "-version"], capture_output=True, text=True, timeout=8, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first_line = (completed.stdout or "").splitlines()
    if not first_line:
        return None
    parts = first_line[0].split()
    return parts[2] if len(parts) > 2 else first_line[0]


def collect_components() -> list[Component]:
    components: list[Component] = [
        Component(name="Python", version=platform.python_version(), available=True)
    ]
    for distribution, label in COMPONENTS.items():
        version = _version(distribution)
        if version is None and distribution == "onnxruntime-gpu":
            version = _version("onnxruntime")
            if version is not None:
                label = "ONNX Runtime (CPU)"
        components.append(Component(name=label, version=version or "-", available=version is not None))

    ffmpeg = ffmpeg_version()
    components.append(
        Component(name="FFmpeg", version=ffmpeg or "-", available=ffmpeg is not None)
    )
    return components


def torch_runtime() -> dict:
    """CUDA / cuDNN details straight from PyTorch (empty when torch is absent)."""
    info: dict = {"available": False}
    try:
        import torch  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return info
    info["available"] = True
    info["torch"] = torch.__version__
    info["cuda_available"] = bool(torch.cuda.is_available())
    info["cuda_runtime"] = getattr(torch.version, "cuda", None)
    info["cudnn"] = getattr(torch.backends.cudnn, "version", lambda: None)()
    try:
        import torchvision  # noqa: PLC0415

        info["torchvision"] = torchvision.__version__
    except Exception:  # noqa: BLE001
        info["torchvision"] = None
    if info["cuda_available"]:
        try:
            info["device_name"] = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            info["device_capability"] = f"{props.major}.{props.minor}"
            info["device_memory_gb"] = round(props.total_memory / (1024**3), 2)
        except Exception:  # noqa: BLE001
            pass
    return info


def compatibility_report() -> dict:
    """Compare the AI stack versions and flag known-incompatible combinations."""
    versions = {name: _version(name) for name in ("torch", "torchvision", "onnxruntime-gpu")}
    if versions["onnxruntime-gpu"] is None:
        versions["onnxruntime-gpu"] = _version("onnxruntime")

    issues: list[str] = []
    notes: list[str] = []

    torch_version = versions.get("torch")
    tv_version = versions.get("torchvision")
    if torch_version and tv_version:
        # TorchVision releases are pinned to a Torch minor version.
        torch_minor = ".".join(torch_version.split(".")[:2])
        expected = {
            "2.5": "0.20",
            "2.4": "0.19",
            "2.3": "0.18",
            "2.6": "0.21",
            "2.7": "0.22",
        }.get(torch_minor)
        if expected and not tv_version.startswith(expected):
            issues.append(
                f"torch {torch_version} با torchvision {tv_version} سازگار نیست؛ "
                f"نسخه مورد انتظار {expected}.x است"
            )
    elif torch_version and not tv_version:
        notes.append("torchvision نصب نیست؛ RF-DETR به آن نیاز دارد")

    if torch_version and "+cu" in torch_version:
        notes.append(f"نسخه CUDA داخل PyTorch: {torch_version.split('+')[1]}")
    elif torch_version:
        notes.append("PyTorch بدون پشتیبانی CUDA نصب شده است")

    if versions.get("onnxruntime-gpu") is None:
        notes.append("ONNX Runtime نصب نیست (فقط برای استقرار ONNX لازم است)")

    runtime = torch_runtime()
    if runtime.get("cuda_available"):
        notes.append(
            f"CUDA در دسترس است: {runtime.get('cuda_runtime')} روی {runtime.get('device_name')}"
        )
    else:
        notes.append("CUDA در دسترس نیست؛ پردازش روی CPU انجام می‌شود")

    cpu = platform.processor() or platform.machine()
    return {
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "cpu": cpu,
        "versions": versions,
        "torch_runtime": runtime,
        "issues": issues,
        "notes": notes,
    }
