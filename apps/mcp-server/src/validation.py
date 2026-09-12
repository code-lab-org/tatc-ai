"""Validation utilities for satellite data and parameters."""

import math
from datetime import datetime, timedelta
from typing import Tuple, Optional

MAX_GROUND_TRACK_POINTS = 2000


def validate_norad_id(norad_id: int) -> int:
    """Validate a NORAD catalog number (1-99999). Accepts numeric strings."""
    if isinstance(norad_id, bool):
        raise ValueError(f"NORAD ID must be an integer, got {type(norad_id)}")
    if isinstance(norad_id, float):
        if not norad_id.is_integer():
            raise ValueError(f"NORAD ID must be an integer, got {norad_id}")
        norad_id = int(norad_id)
    if not isinstance(norad_id, int):
        try:
            norad_id = int(norad_id)
        except (ValueError, TypeError):
            raise ValueError(f"NORAD ID must be an integer, got {type(norad_id)}")

    if norad_id < 1 or norad_id > 99999:
        raise ValueError(f"NORAD ID must be between 1 and 99999, got {norad_id}")

    return norad_id


def _tle_checksum(line: str) -> int:
    """Compute the TLE checksum of the first 68 characters of a line."""
    total = 0
    for char in line[:68]:
        if char.isdigit():
            total += int(char)
        elif char == "-":
            total += 1
    return total % 10


def validate_tle_format(tle_line1: str, tle_line2: str) -> Tuple[str, str]:
    """Validate two TLE lines, including real checksum digits. Returns them stripped."""
    if not isinstance(tle_line1, str) or not isinstance(tle_line2, str):
        raise ValueError("TLE lines must be strings")

    tle_line1 = tle_line1.strip()
    tle_line2 = tle_line2.strip()

    if len(tle_line1) < 69:
        raise ValueError(
            f"TLE line 1 too short: {len(tle_line1)} characters (minimum 69)"
        )

    if len(tle_line2) < 69:
        raise ValueError(
            f"TLE line 2 too short: {len(tle_line2)} characters (minimum 69)"
        )

    if not tle_line1.startswith("1 "):
        raise ValueError("TLE line 1 must start with '1 '")

    if not tle_line2.startswith("2 "):
        raise ValueError("TLE line 2 must start with '2 '")

    # Real checksum validation over the first 68 characters
    try:
        checksum1 = int(tle_line1[-1])
        checksum2 = int(tle_line2[-1])
    except ValueError:
        raise ValueError("TLE checksums must be digits") from None

    if _tle_checksum(tle_line1) != checksum1:
        raise ValueError(
            f"TLE line 1 checksum mismatch: expected {_tle_checksum(tle_line1)}, "
            f"got {checksum1}"
        )

    if _tle_checksum(tle_line2) != checksum2:
        raise ValueError(
            f"TLE line 2 checksum mismatch: expected {_tle_checksum(tle_line2)}, "
            f"got {checksum2}"
        )

    return tle_line1, tle_line2


def validate_time_range(
    start_time: datetime,
    end_time: datetime,
    max_duration: Optional[timedelta] = None,
) -> Tuple[datetime, datetime]:
    """Validate a time range. Caps duration at 30 days unless max_duration says otherwise."""
    if not isinstance(start_time, datetime):
        raise ValueError(
            f"start_time must be a datetime object, got {type(start_time)}"
        )

    if not isinstance(end_time, datetime):
        raise ValueError(f"end_time must be a datetime object, got {type(end_time)}")

    if end_time <= start_time:
        raise ValueError(
            f"end_time must be after start_time: {start_time} >= {end_time}"
        )

    duration = end_time - start_time

    if max_duration is not None and duration > max_duration:
        raise ValueError(
            f"Duration {duration} exceeds maximum allowed duration {max_duration}"
        )

    # Check for reasonable duration (not more than 30 days)
    max_reasonable = timedelta(days=30)
    if duration > max_reasonable:
        raise ValueError(
            f"Duration {duration} is unreasonably long (maximum {max_reasonable})"
        )

    return start_time, end_time


def validate_step_interval(
    step_seconds: float,
    min_step: float = 1.0,
    max_step: float = 3600.0,
) -> float:
    """Validate a step interval in seconds (default bounds: 1s to 1h)."""
    if isinstance(step_seconds, bool):
        raise ValueError(f"step_seconds must be a number, got {type(step_seconds)}")
    if not isinstance(step_seconds, (int, float)):
        try:
            step_seconds = float(step_seconds)
        except (ValueError, TypeError):
            raise ValueError(f"step_seconds must be a number, got {type(step_seconds)}")

    if not math.isfinite(step_seconds):
        raise ValueError(f"step_seconds must be finite, got {step_seconds}")

    if step_seconds < min_step:
        raise ValueError(
            f"Step interval {step_seconds}s is too small (minimum {min_step}s)"
        )

    if step_seconds > max_step:
        raise ValueError(
            f"Step interval {step_seconds}s is too large (maximum {max_step}s)"
        )

    return float(step_seconds)


def validate_ground_track_sample_count(
    start_time: datetime,
    end_time: datetime,
    step_seconds: float,
    max_points: int = MAX_GROUND_TRACK_POINTS,
) -> int:
    """Validate and return the inclusive number of ground-track samples.

    The effective step uses the same ``timedelta`` conversion as the sampling
    loop, so values rounded to zero cannot create an endless loop.
    """
    if not isinstance(start_time, datetime):
        raise ValueError(
            f"start_time must be a datetime object, got {type(start_time)}"
        )
    if not isinstance(end_time, datetime):
        raise ValueError(f"end_time must be a datetime object, got {type(end_time)}")

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

    try:
        effective_step = timedelta(seconds=step_seconds)
    except OverflowError as exc:
        raise ValueError(
            f"step_seconds is too large to represent as a timedelta: {step_seconds}"
        ) from exc
    if effective_step <= timedelta(0):
        raise ValueError(
            "step_seconds must result in a positive timedelta after rounding"
        )

    duration = end_time - start_time
    if duration < timedelta(0):
        raise ValueError("end_time must be on or after start_time")

    sample_count = int(duration // effective_step) + 1
    if sample_count > max_points:
        raise ValueError(
            f"Requested {sample_count} ground-track points exceeds the maximum "
            f"of {max_points}. Shorten the duration or increase the step interval."
        )

    return sample_count


def validate_coordinates(lat_deg: float, lon_deg: float) -> Tuple[float, float]:
    """Validate coordinates; normalize longitude to [-180, 180]."""
    if isinstance(lat_deg, bool) or not isinstance(lat_deg, (int, float)):
        raise ValueError(f"Latitude must be a number, got {type(lat_deg)}")

    if isinstance(lon_deg, bool) or not isinstance(lon_deg, (int, float)):
        raise ValueError(f"Longitude must be a number, got {type(lon_deg)}")

    lat_deg = float(lat_deg)
    lon_deg = float(lon_deg)

    if not math.isfinite(lat_deg) or not math.isfinite(lon_deg):
        raise ValueError(f"Coordinates must be finite, got ({lat_deg}, {lon_deg})")

    if not (-90 <= lat_deg <= 90):
        raise ValueError(f"Latitude {lat_deg} is out of valid range [-90, 90]")

    # Normalize longitude to [-180, 180]
    if lon_deg < -180 or lon_deg > 180:
        lon_deg = ((lon_deg + 180) % 360) - 180

    return lat_deg, lon_deg


def validate_altitude(
    alt_m: float,
    min_alt: Optional[float] = None,
    max_alt: Optional[float] = None,
) -> float:
    """Validate an altitude in meters against optional bounds."""
    if isinstance(alt_m, bool) or not isinstance(alt_m, (int, float)):
        raise ValueError(f"Altitude must be a number, got {type(alt_m)}")

    alt_m = float(alt_m)

    if not math.isfinite(alt_m):
        raise ValueError(f"Altitude must be finite, got {alt_m}")

    if min_alt is not None and alt_m < min_alt:
        raise ValueError(f"Altitude {alt_m}m is below minimum {min_alt}m")

    if max_alt is not None and alt_m > max_alt:
        raise ValueError(f"Altitude {alt_m}m is above maximum {max_alt}m")

    return alt_m
