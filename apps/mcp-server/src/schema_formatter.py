"""Format TAT-C outputs to match server telemetry format specification."""

import logging
import math
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

from shapely import unary_union
from shapely.affinity import translate
from shapely.geometry import Polygon, box
from shapely.geometry.polygon import orient

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


def _split_footprint_at_dateline(coordinates: List[List[float]]) -> Optional[Dict[str, Any]]:
    """Represent a small spherical footprint in the GeoJSON longitude domain.

    Unwrap short edges before planar clipping. A ring winding around a pole
    must close through that pole, not through the interior of its small circle.
    Cut at +/-180 as recommended by RFC 7946 section 3.1.9.
    """
    unwrapped = [list(coordinates[0])]
    for lon, lat in coordinates[1:]:
        previous_lon = unwrapped[-1][0]
        delta = (lon - previous_lon + 180) % 360 - 180
        unwrapped.append([previous_lon + delta, lat])

    if abs(unwrapped[-1][0] - unwrapped[0][0]) > 180:
        pole = 90.0 if sum(lat for _, lat in coordinates[:-1]) > 0 else -90.0
        unwrapped.extend([[unwrapped[-1][0], pole], [unwrapped[0][0], pole]])

    polygon = Polygon(unwrapped)
    if not polygon.is_valid or polygon.area == 0:
        return None
    first_strip = math.floor((polygon.bounds[0] + 180) / 360)
    last_strip = math.floor((polygon.bounds[2] + 180) / 360)
    pieces = []
    for strip in range(first_strip, last_strip + 1):
        offset = 360 * strip
        clipped = polygon.intersection(box(-180 + offset, -90, 180 + offset, 90))
        if clipped.area > 0:
            pieces.append(translate(clipped, xoff=-offset))

    # Rejoin the artificial cut at the ring's starting longitude for polar
    # caps. Dateline pieces stay separate at opposite ends of the map.
    # Use the output precision for overlay, so floating-point translation
    # cannot leave a microscopic gap that turns into a shared edge on rounding.
    merged = unary_union(pieces, grid_size=0.0001)
    polygons = [merged] if isinstance(merged, Polygon) else list(merged.geoms)
    rings = []
    for part in polygons:
        if not isinstance(part, Polygon) or part.area == 0:
            continue
        part = orient(part, sign=1.0)
        rings.append([
            [[round(lon, 4), round(lat, 4)] for lon, lat in ring.coords]
            for ring in [part.exterior, *part.interiors]
        ])
    if not rings:
        return None
    if len(rings) == 1:
        return {"type": "Polygon", "coordinates": rings[0]}
    return {"type": "MultiPolygon", "coordinates": rings}


def format_footprint_geojson(
    coordinates: List[List[float]],
) -> Optional[Dict[str, Any]]:
    """
    Format a footprint as a GeoJSON Polygon or MultiPolygon feature.
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

    # Round to 4 decimals (~11 m) to keep tool results compact.
    rounded = [[round(lon, 4), round(lat, 4)] for lon, lat in validated_coords]

    geometry = {"type": "Polygon", "coordinates": [rounded]}
    if any(abs(a[0] - b[0]) > 180 for a, b in zip(validated_coords, validated_coords[1:])):
        geometry = _split_footprint_at_dateline(validated_coords)
        if geometry is None:
            return None

    # Create a GeoJSON Feature with Polygon or MultiPolygon geometry.
    # Per server telemetry format: coordinates in [lon, lat] (WGS84), properties must be {}
    return {
        "type": "Feature",
        "geometry": geometry,
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
