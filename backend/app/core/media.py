"""Helpers to turn stored relative media paths into servable URLs.

Paths stored in the database are always **relative to**
``settings.storage_root`` so the whole project (database included) can be
moved between machines, which matters a lot for offline installs.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from app.core.config import settings

MEDIA_PREFIX = "/media"


def relativize(path: str | Path) -> str:
    """Return a storage-relative, POSIX-style path."""
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(settings.storage_root.resolve())
        except ValueError:
            # Outside the storage root (e.g. an imported model file): keep the
            # string as-is, it simply won't be served over /media.
            return p.as_posix()
    return PurePosixPath(p.as_posix().lstrip("./")).as_posix()


def absolutize(relative: str | None) -> Path | None:
    if not relative:
        return None
    p = Path(relative)
    if p.is_absolute():
        return p
    return settings.storage_root / p


def media_url(relative: str | None) -> str | None:
    if not relative:
        return None
    rel = relativize(relative)
    if rel.startswith("/") or ".." in rel:
        return None
    return f"{MEDIA_PREFIX}/{rel}"
