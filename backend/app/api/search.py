"""Spatial search: draw a region, pick time/type/direction, get the objects."""

from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.timeutil import jalali_datetime_label
from app.models.detection import TrackRecord
from app.schemas.detection import SpatialSearchRequest, SpatialSearchResult
from app.services.geometry import direction_label_fa, point_in_polygon
from app.services.models_registry import class_label
from app.services.search import build_details, query_tracks

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("/spatial", response_model=SpatialSearchResult)
async def spatial_search(
    payload: SpatialSearchRequest,
    session: AsyncSession = Depends(get_session),
) -> SpatialSearchResult:
    total, tracks = await query_tracks(
        session,
        camera_ids=payload.camera_ids,
        polygon=payload.polygon,
        start=payload.start,
        end=payload.end,
        object_types=payload.object_types,
        directions=payload.directions,
        motion_state=payload.motion_state,
        min_confidence=payload.min_confidence,
        min_quality=payload.min_quality,
        limit=payload.limit,
        offset=payload.offset,
        order=payload.order,
    )
    details = await build_details(session, tracks)
    return SpatialSearchResult(total=total, items=details, polygon=payload.polygon)


@router.post("/spatial/export")
async def export_spatial(
    payload: SpatialSearchRequest,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Same query, streamed as UTF-8 CSV (Excel friendly, with a BOM)."""
    _total, tracks = await query_tracks(
        session,
        camera_ids=payload.camera_ids,
        polygon=payload.polygon,
        start=payload.start,
        end=payload.end,
        object_types=payload.object_types,
        directions=payload.directions,
        motion_state=payload.motion_state,
        min_confidence=payload.min_confidence,
        min_quality=payload.min_quality,
        limit=min(payload.limit, 5000),
        offset=0,
        order=payload.order,
    )

    def rows():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "id",
                "camera_id",
                "type",
                "type_fa",
                "direction",
                "direction_fa",
                "motion_state",
                "confidence",
                "first_seen_utc",
                "first_seen_jalali",
                "duration_seconds",
                "center_x",
                "center_y",
                "distance",
            ]
        )
        yield "\ufeff" + buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        for track in tracks:
            writer.writerow(
                [
                    track.id,
                    track.camera_id,
                    track.object_type,
                    class_label(track.object_type),
                    track.direction,
                    direction_label_fa(track.direction),
                    track.motion_state,
                    f"{track.confidence:.3f}",
                    track.first_seen.isoformat(),
                    jalali_datetime_label(track.first_seen),
                    f"{track.duration_seconds:.2f}",
                    f"{track.center_x:.4f}" if track.center_x is not None else "",
                    f"{track.center_y:.4f}" if track.center_y is not None else "",
                    f"{track.distance_travelled:.4f}",
                ]
            )
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)

    filename = "camerasmart-spatial-search.csv"
    return StreamingResponse(
        rows(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/point", response_model=dict)
async def point_query(
    payload: SpatialSearchRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Lightweight variant used by the map/heatmap click-through."""
    total, tracks = await query_tracks(
        session,
        camera_ids=payload.camera_ids,
        polygon=payload.polygon,
        start=payload.start,
        end=payload.end,
        object_types=payload.object_types,
        directions=payload.directions,
        limit=min(payload.limit, 500),
        offset=0,
        order=payload.order,
    )
    type_counts: dict[str, int] = {}
    direction_counts: dict[str, int] = {}
    for track in tracks:
        type_counts[track.object_type] = type_counts.get(track.object_type, 0) + 1
        direction_counts[track.direction] = direction_counts.get(track.direction, 0) + 1
    return {
        "total": total,
        "by_type": type_counts,
        "by_direction": direction_counts,
        "sample": [
            {
                "id": track.id,
                "object_type": track.object_type,
                "direction": track.direction,
                "first_seen": track.first_seen.isoformat(),
                "center": [track.center_x, track.center_y],
                "bbox": json.loads(json.dumps(track.bbox_last)),
            }
            for track in tracks[:50]
        ],
    }


def _contains(track: TrackRecord, polygon: list[list[float]] | None) -> bool:
    if not polygon or track.center_x is None:
        return True
    return point_in_polygon((track.center_x, track.center_y or 0.0), polygon)
