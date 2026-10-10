"""Format TAT-C outputs to match server telemetry format specification."""

import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

from shapely.geometry import mapping

from .validation import validate_altitude as _validate_altitude
from .validation import validate_coordinates

logger = logging.getLogger(__name__)


def format_timestamp(time: datetime) -> str:
    """Format a datetime as an ISO-8601 UTC string with trailing 'Z'."""
    if not isinstance(time, datetime):
        raise ValueError(f"time must be a datetime object, got {type(time)}")
    if time.tzinfo is None:
        time = time.replace(tzinfo=timezone.utc)
    else:
        time = time.astimezone(timezone.utc)

    # server telemetry format requires strict UTC timestamps with trailing Z.
    time = time.replace(microsecond=0)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_position_lla(
    lat_deg: float, lon_deg: float, alt_m: float
) -> Dict[str, float]:
    """Format a position as an LLA dict per the server telemetry format."""
    lat_deg, lon_deg = validate_coordinates(lat_deg, lon_deg)
    alt_m = _validate_altitude(alt_m)

    # Round for compact output: 4 decimals of degrees is ~11 m, 1 decimal
    # of altitude is 10 cm. Full float precision only bloats tool results
    # that models must fit in context.
    return {
        "lat_deg": round(float(lat_deg), 4),
        "lon_deg": round(float(lon_deg), 4),
        "alt_m": round(float(alt_m), 1),
    }


def format_footprint_geojson(footprint: Any) -> Optional[Dict[str, Any]]:
    """Format a TAT-C footprint as a GeoJSON Feature, or None if empty.

    TAT-C returns a Polygon, or a MultiPolygon when the footprint crosses
    the antimeridian. Coordinates are [lon, lat] rounded to 4 decimals
    (~11 m) to keep tool results compact; the zero elevation is dropped.
    """
    if footprint is None or getattr(footprint, "is_empty", True):
        return None
    geometry = mapping(footprint)
    if geometry["type"] not in ("Polygon", "MultiPolygon"):
        return None

    def ring(coords):
        return [[round(float(c[0]), 4), round(float(c[1]), 4)] for c in coords]

    if geometry["type"] == "Polygon":
        coordinates = [ring(r) for r in geometry["coordinates"]]
    else:
        coordinates = [[ring(r) for r in poly] for poly in geometry["coordinates"]]
    # Per server telemetry format: coordinates in [lon, lat] (WGS84), properties must be {}
    return {
        "type": "Feature",
        "geometry": {"type": geometry["type"], "coordinates": coordinates},
        "properties": {},
    }


def format_trajectory_batch(
    ground_track: List[Tuple[datetime, float, float, float]],
) -> List[Dict[str, Any]]:
    """Format a ground track as a trajectory_batches array."""
    batches = []
    for entry in ground_track:
        try:
            time, lat_deg, lon_deg, alt_m = entry
            batches.append(
                {
                    "time": format_timestamp(time),
                    "position_lla": format_position_lla(lat_deg, lon_deg, alt_m),
                }
            )
        except (ValueError, TypeError) as e:
            # Skip invalid coordinates; log to stderr, never stdout, so
            # MCP stdio framing stays intact.
            logger.warning("Skipping invalid trajectory point: %s", e)
            continue

    return batches


def format_telemetry_message(
    satellite_id: str,
    time: datetime,
    position_lla: Tuple[float, float, float],
    footprint: Optional[Any] = None,
    trajectory_batches: Optional[List[Tuple[datetime, float, float, float]]] = None,
    lookpoint_lla: Optional[Tuple[float, float, float]] = None,
    state_flags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Format one telemetry message per the server telemetry format."""
    if not satellite_id or not satellite_id.strip():
        raise ValueError("satellite_id must be a non-empty string")

    lat_deg, lon_deg, alt_m = position_lla

    # Build base message
    message = {
        "id": str(satellite_id).strip(),
        "time": format_timestamp(time),
        "position_lla": format_position_lla(lat_deg, lon_deg, alt_m),
    }

    # Add optional lookpoint_lla
    if lookpoint_lla is not None:
        look_lat, look_lon, look_alt = lookpoint_lla
        message["lookpoint_lla"] = format_position_lla(look_lat, look_lon, look_alt)

    # Add optional footprint_geojson
    if footprint is not None:
        footprint_geojson = format_footprint_geojson(footprint)
        if footprint_geojson is not None:
            message["footprint_geojson"] = footprint_geojson

    # Add optional state_flags
    if state_flags is not None and len(state_flags) > 0:
        message["state_flags"] = [str(flag) for flag in state_flags]

    # Add optional trajectory_batches
    if trajectory_batches is not None:
        message["trajectory_batches"] = format_trajectory_batch(trajectory_batches)

    return message


def format_ground_track_response(
    satellite_id: str,
    ground_track: List[Tuple[datetime, float, float, float, Optional[Any]]],
) -> List[Dict[str, Any]]:
    """Format (time, lat, lon, alt, footprint) samples as telemetry messages."""
    messages = []
    for time, lat_deg, lon_deg, alt_m, footprint in ground_track:
        try:
            messages.append(
                format_telemetry_message(
                    satellite_id=satellite_id,
                    time=time,
                    position_lla=(lat_deg, lon_deg, alt_m),
                    footprint=footprint,
                )
            )
        except ValueError as e:
            logger.warning("Skipping invalid telemetry point at %s: %s", time, e)
            continue

    return messages
