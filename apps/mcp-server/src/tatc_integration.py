"""TAT-C library integration for satellite operations."""

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Optional, Any

from .validation import validate_ground_track_sample_count

logger = logging.getLogger(__name__)

# Constants
DEFAULT_STEP_SECONDS = 60.0  # Default time step for ground track generation
DEFAULT_FOV_DEGREES = 60.0  # Default field of view for footprint calculation
FOOTPRINT_POLYGON_POINTS = 16  # Number of points in circular footprint polygon
EARTH_RADIUS_M = 6371000.0  # Earth radius in meters for footprint calculations

try:
    from tatc.schemas import GeneralPerturbationsOrbit

    _HAS_GPO = True
except ImportError:
    GeneralPerturbationsOrbit = None  # type: ignore[assignment]
    _HAS_GPO = False

try:
    from tatc.schemas import TwoLineElements  # tatc < 3.5

    _HAS_TLE = True
except ImportError:
    TwoLineElements = None  # type: ignore[assignment]
    _HAS_TLE = False

try:
    from skyfield.api import wgs84

    _HAS_WGS84 = True
except ImportError:
    wgs84 = None  # type: ignore[assignment]
    _HAS_WGS84 = False

TATC_AVAILABLE = (_HAS_GPO or _HAS_TLE) and _HAS_WGS84


def _require_tatc() -> None:
    """Raise ImportError unless tatc and skyfield both imported."""
    if not TATC_AVAILABLE:
        raise ImportError(
            "TAT-C libraries (tatc and skyfield) are not installed. "
            "Install them with: pip install tatc"
        )


def _ensure_utc(time: datetime) -> datetime:
    """Convert datetime to UTC timezone-aware."""
    if not isinstance(time, datetime):
        raise ValueError(f"time must be a datetime object, got {type(time)}")
    if time.tzinfo is None:
        return time.replace(tzinfo=timezone.utc)
    return time.astimezone(timezone.utc)


def _extract_lla(subpoint) -> Tuple[float, float, float]:
    """Extract lat/lon/alt from Skyfield subpoint."""
    return (
        float(subpoint.latitude.degrees),
        float(subpoint.longitude.degrees),
        float(subpoint.elevation.m),
    )


def create_satellite_from_tle(tle_line1: str, tle_line2: str) -> Any:
    """Create a TAT-C orbit object from TLE lines.

    Uses GeneralPerturbationsOrbit on tatc>=3.5, TwoLineElements on older releases.
    """
    _require_tatc()

    try:
        if _HAS_GPO:
            return GeneralPerturbationsOrbit.from_tle([tle_line1, tle_line2])
        # Fallback for tatc < 3.5
        tle = TwoLineElements(tle=[tle_line1, tle_line2])
        return tle
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Both tatc APIs raise their own error types for bad TLE data.
        raise ValueError(f"Failed to create satellite from TLE: {e}") from e


def propagate_satellite(
    satellite: Any,
    time: datetime,
) -> Tuple[float, float, float]:
    """
    Propagate a satellite to a time and return (lat_deg, lon_deg, alt_m).
    Naive datetimes are assumed UTC.
    """
    _require_tatc()

    try:
        time = _ensure_utc(time)
        track = satellite.get_orbit_track(time)  # Get orbit position at time
        subpoint = wgs84.subpoint(track)  # Get ground point directly below satellite
        return _extract_lla(subpoint)
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Normalize third-party propagation failures to ValueError.
        raise ValueError(f"Failed to propagate satellite: {e}") from e


def generate_ground_track(
    satellite: Any,
    start_time: datetime,
    end_time: datetime,
    step_seconds: float = DEFAULT_STEP_SECONDS,
) -> List[Tuple[datetime, float, float, float]]:
    """
    Generate a ground track over a time range.

    Returns a list of (time, lat_deg, lon_deg, alt_m) tuples with naive UTC times.
    Raises ValueError if the step is not positive or no points could be produced.
    """
    _require_tatc()

    try:
        step_seconds = float(step_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"step_seconds must be a number, got {step_seconds!r}"
        ) from exc
    if not math.isfinite(step_seconds) or step_seconds <= 0:
        raise ValueError(
            f"step_seconds must be a positive finite number, got {step_seconds}"
        )

    start_time = _ensure_utc(start_time)
    end_time = _ensure_utc(end_time)
    validate_ground_track_sample_count(start_time, end_time, step_seconds)

    # Generate time sequence
    times = []
    current_time = start_time
    while current_time <= end_time:
        times.append(current_time)
        current_time += timedelta(seconds=step_seconds)

    # Try batch propagation (faster than individual calls)
    try:
        track = satellite.get_orbit_track(times)

        # Handle both single object and collection responses
        try:
            track_points = list(track)
        except (TypeError, AttributeError):
            track_points = [track]

        ground_track = []
        for i, time in enumerate(times):
            try:
                # Use corresponding point or last point if index out of range
                point = track_points[i] if i < len(track_points) else track_points[-1]
                subpoint = wgs84.subpoint(point)
                lat_deg, lon_deg, alt_m = _extract_lla(subpoint)

                # Convert to naive UTC (timezone removed) for compatibility
                ground_track.append(
                    (time.replace(tzinfo=None), lat_deg, lon_deg, alt_m)
                )
            except Exception as e:  # pylint: disable=broad-exception-caught
                # One bad sample must not kill the whole track; third-party
                # propagation can fail per-point.
                logger.warning("Failed to extract coordinates at %s: %s", time, e)
                continue

        if not ground_track:
            raise ValueError("Batch propagation produced no usable points")
        return ground_track

    except ValueError:
        raise
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Batch shape varies across tatc releases; fall back to serial calls.
        logger.warning("Batch propagation failed, using individual propagation: %s", e)
        ground_track = []
        last_error: Optional[Exception] = e
        for time in times:
            try:
                lat_deg, lon_deg, alt_m = propagate_satellite(satellite, time)
                ground_track.append(
                    (time.replace(tzinfo=None), lat_deg, lon_deg, alt_m)
                )
            except Exception as point_error:  # pylint: disable=broad-exception-caught
                logger.warning("Failed to propagate at %s: %s", time, point_error)
                last_error = point_error
                continue

        if not ground_track:
            raise ValueError(
                f"Failed to propagate satellite at any requested time: {last_error}"
            )

        return ground_track


def calculate_footprint_from_position(
    lat_deg: float,
    lon_deg: float,
    alt_m: float,
    fov_degrees: Optional[float] = None,
) -> Optional[List[List[float]]]:
    """
    Calculate a footprint polygon from an already propagated position.

    Returns [lon, lat] ring coordinates, or None if it fails.
    """
    try:
        return _calculate_circular_footprint(lat_deg, lon_deg, alt_m, fov_degrees)
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Footprint is optional output; never fail the whole request for it.
        logger.warning("Footprint calculation failed from propagated position: %s", e)
        return None


def _calculate_circular_footprint(
    lat_deg: float,
    lon_deg: float,
    alt_m: float,
    fov_degrees: Optional[float] = None,
) -> List[List[float]]:
    """
    Calculate a simple circular footprint approximation as [lon, lat] coordinates.
    """
    if fov_degrees is None:
        fov_degrees = DEFAULT_FOV_DEGREES

    # Calculate footprint radius on Earth's surface using geometric approximation.
    # When the field of view reaches past the horizon (high altitudes), clamp
    # to the horizon circle instead of failing in asin.
    fov_rad = math.radians(fov_degrees / 2.0)
    if alt_m > 0:
        # Account for satellite altitude in footprint radius calculation
        sin_term = (EARTH_RADIUS_M + alt_m) * math.sin(fov_rad) / EARTH_RADIUS_M
        if sin_term >= 1.0:
            footprint_radius_rad = math.acos(EARTH_RADIUS_M / (EARTH_RADIUS_M + alt_m))
        else:
            footprint_radius_rad = math.asin(sin_term) - fov_rad
    else:
        footprint_radius_rad = fov_rad

    footprint_radius_deg = math.degrees(footprint_radius_rad)

    # Generate circular polygon by sampling points around the center.
    # The closing point is an exact copy of the first: recomputing the
    # angle at 2*pi drifts by a floating-point epsilon (worse after the
    # longitude modulo), which would leave the ring technically unclosed.
    coords = []
    for i in range(FOOTPRINT_POLYGON_POINTS):
        angle = 2 * math.pi * i / FOOTPRINT_POLYGON_POINTS
        # Calculate lat/lon offset (simplified approximation)
        dlat = footprint_radius_deg * math.cos(angle)
        cos_lat = math.cos(math.radians(lat_deg))
        if abs(cos_lat) < 1e-6:
            dlon = 0.0
        else:
            dlon = footprint_radius_deg * math.sin(angle) / cos_lat

        # Normalize coordinates to valid ranges
        new_lat = max(-90, min(90, lat_deg + dlat))
        new_lon = ((lon_deg + dlon + 180) % 360) - 180

        coords.append([new_lon, new_lat])

    coords.append(list(coords[0]))
    return coords
