"""TAT-C library integration for satellite operations.

Orbits, ground tracks, and footprints all come from TAT-C: this module only
builds the TAT-C objects and turns its GeoDataFrames into plain tuples.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from tatc.analysis import collect_ground_track, collect_orbit_track
from tatc.schemas import GeneralPerturbationsOrbit, Instrument, Satellite

from .validation import validate_ground_track_sample_count

DEFAULT_STEP_SECONDS = 60.0  # Default time step for ground track generation
# Full angle of the footprint cone drawn around each ground-track point. Only
# used for the optional map footprint; coverage takes explicit geometry.
DEFAULT_FOOTPRINT_FOR_DEG = 60.0


def create_orbit_from_omm(omm_records: List[Dict[str, Any]]) -> Any:
    """Create a TAT-C GeneralPerturbationsOrbit from OMM element records."""
    try:
        return GeneralPerturbationsOrbit.from_omm_json(json.dumps(omm_records))
    except Exception as e:  # pylint: disable=broad-exception-caught
        # TAT-C and sgp4 raise their own error types for bad element data.
        raise ValueError(f"Failed to create orbit from OMM data: {e}") from e


def _ensure_utc(time: datetime) -> datetime:
    """Convert datetime to UTC timezone-aware; naive values are taken as UTC."""
    if not isinstance(time, datetime):
        raise ValueError(f"time must be a datetime object, got {type(time)}")
    if time.tzinfo is None:
        return time.replace(tzinfo=timezone.utc)
    return time.astimezone(timezone.utc)


def _sample_times(
    start_time: datetime, end_time: datetime, step_seconds: float
) -> List[datetime]:
    start_time = _ensure_utc(start_time)
    end_time = _ensure_utc(end_time)
    count = validate_ground_track_sample_count(start_time, end_time, step_seconds)
    step = timedelta(seconds=float(step_seconds))
    return [start_time + i * step for i in range(count)]


def _satellite(orbit: Any, field_of_regard: float) -> Satellite:
    return Satellite(
        name="ground track",
        orbit=orbit,
        instruments=[Instrument(name="footprint", field_of_regard=field_of_regard)],
    )


def generate_ground_track(
    orbit: Any,
    start_time: datetime,
    end_time: datetime,
    step_seconds: float = DEFAULT_STEP_SECONDS,
    include_footprint: bool = False,
    footprint_field_of_regard: float = DEFAULT_FOOTPRINT_FOR_DEG,
) -> List[Tuple[datetime, float, float, float, Optional[Any]]]:
    """Sample a ground track with TAT-C, optionally with footprints.

    Positions come from TAT-C collect_orbit_track in WGS84. Footprints come
    from TAT-C collect_ground_track, which projects the instrument cone onto
    the WGS84 ellipsoid and splits polygons at the antimeridian.

    Returns:
        (naive UTC time, lat_deg, lon_deg, alt_m, footprint) tuples, where
        footprint is a shapely Polygon or MultiPolygon, or None.
    """
    times = _sample_times(start_time, end_time, step_seconds)
    satellite = _satellite(orbit, footprint_field_of_regard)
    try:
        positions = collect_orbit_track(satellite, times)
        footprints = (
            list(collect_ground_track(satellite, times).geometry)
            if include_footprint
            else [None] * len(times)
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Normalize third-party propagation failures to ValueError.
        raise ValueError(f"Failed to propagate satellite: {e}") from e
    return [
        (time.replace(tzinfo=None), float(point.y), float(point.x), float(point.z), footprint)
        for time, point, footprint in zip(times, positions.geometry, footprints)
    ]
