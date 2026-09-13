"""Format TAT-C outputs to match server telemetry format specification."""

import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

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

    return {
        "lat_deg": float(lat_deg),
        "lon_deg": float(lon_deg),
        "alt_m": float(alt_m),
    }


def format_footprint_geojson(
    coordinates: List[List[float]],
) -> Optional[Dict[str, Any]]:
    """
    Format [lon, lat] coordinates as a GeoJSON Feature<Polygon>, or None if invalid.
    """
    if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 3:
        return None

    # Validate and normalize coordinates
    validated_coords = []
    for coord in coordinates:
        if not isinstance(coord, (list, tuple)) or len(coord) < 2:
            continue
        lon, lat = coord[0], coord[1]
        try:
            lat, lon = validate_coordinates(lat, lon)
            validated_coords.append([lon, lat])
        except (ValueError, TypeError):
            continue

    if len(validated_coords) < 3:
        return None

    # Ensure polygon is closed (first point == last point)
    if validated_coords[0] != validated_coords[-1]:
        validated_coords.append(validated_coords[0])

    # Create GeoJSON Feature<Polygon>
    # Per server telemetry format: coordinates in [lon, lat] (WGS84), properties must be {}
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                validated_coords
            ],  # Polygon coordinates are wrapped in an array
        },
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
    footprint_coords: Optional[List[List[float]]] = None,
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
    if footprint_coords is not None:
        footprint_geojson = format_footprint_geojson(footprint_coords)
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
    ground_track: List[Tuple[datetime, float, float, float]],
    footprints: Optional[List[Optional[List[List[float]]]]] = None,
) -> List[Dict[str, Any]]:
    """Format a ground track as an array of telemetry messages."""
    messages = []

    for i, (time, lat_deg, lon_deg, alt_m) in enumerate(ground_track):
        footprint_coords = None
        if footprints is not None and i < len(footprints):
            footprint_coords = footprints[i]

        try:
            message = format_telemetry_message(
                satellite_id=satellite_id,
                time=time,
                position_lla=(lat_deg, lon_deg, alt_m),
                footprint_coords=footprint_coords,
            )
            messages.append(message)
        except ValueError as e:
            logger.warning("Skipping invalid telemetry point at %s: %s", time, e)
            continue

    return messages
