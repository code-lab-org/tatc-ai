"""Validation utilities for satellite data and parameters."""

import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

MAX_GROUND_TRACK_POINTS = 2000
MAX_NORAD_ID = 999_999_999


def validate_norad_id(norad_id: int) -> int:
    """Validate a NORAD catalog number (1-999999999). Accepts numeric strings.

    The upper bound covers the post-July-2026 six- and nine-digit catalog
    numbers that replaced the exhausted five-digit range.
    """
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

    if norad_id < 1 or norad_id > MAX_NORAD_ID:
        raise ValueError(
            f"NORAD ID must be between 1 and {MAX_NORAD_ID}, got {norad_id}"
        )

    return norad_id


OMM_REQUIRED_FIELDS = (
    "EPOCH",
    "MEAN_MOTION",
    "ECCENTRICITY",
    "INCLINATION",
    "RA_OF_ASC_NODE",
    "ARG_OF_PERICENTER",
    "MEAN_ANOMALY",
    "NORAD_CAT_ID",
)

OMM_NUMERIC_FIELDS = (
    "MEAN_MOTION",
    "ECCENTRICITY",
    "INCLINATION",
    "RA_OF_ASC_NODE",
    "ARG_OF_PERICENTER",
    "MEAN_ANOMALY",
    "BSTAR",
    "MEAN_MOTION_DOT",
    "MEAN_MOTION_DDOT",
)


def validate_omm_records(records: Any) -> List[Dict[str, Any]]:
    """Validate CelesTrak OMM element records, returning them unchanged.

    Checks the fields TAT-C needs to build GP elements, so malformed
    responses fail here with a clear message instead of inside sgp4.
    """
    if not isinstance(records, list) or not records:
        raise ValueError("OMM data must be a non-empty list of element records")

    validated: List[Dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"OMM record {index} must be an object")

        missing = [field for field in OMM_REQUIRED_FIELDS if field not in record]
        if missing:
            raise ValueError(
                f"OMM record {index} is missing required fields: {', '.join(missing)}"
            )

        if not str(record["EPOCH"]).strip():
            raise ValueError(f"OMM record {index} has an empty EPOCH")

        for field in OMM_NUMERIC_FIELDS:
            if field not in record:
                continue
            value = record[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"OMM record {index} field {field} must be a number, got {value!r}"
                )
            if not math.isfinite(float(value)):
                raise ValueError(
                    f"OMM record {index} field {field} must be finite, got {value!r}"
                )

        try:
            validate_norad_id(record["NORAD_CAT_ID"])
        except ValueError as exc:
            raise ValueError(
                f"OMM record {index} has an invalid NORAD_CAT_ID: {exc}"
            ) from exc

        validated.append(record)

    return validated


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
