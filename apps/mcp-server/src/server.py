"""TAT-C MCP server: satellite tools behind the FastMCP stack used by tatc-ai."""

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from dateutil import parser as date_parser
from fastmcp import FastMCP

from . import celestrak_client
from .schema_formatter import format_ground_track_response
from .tatc_integration import (
    calculate_footprint_from_position,
    create_satellite_from_tle,
    generate_ground_track as compute_ground_track,
)
from .validation import (
    validate_ground_track_sample_count,
    validate_step_interval,
    validate_time_range,
)


def _build_auth():
    """OIDC auth against Dex, enabled only when the deploy stack configures it.

    The dev compose file sets none of these, so the dev server stays open.
    """
    issuer_url = os.environ.get("MCP_OIDC_ISSUER_URL")
    if not issuer_url:
        return None

    from fastmcp.server.auth.oidc_proxy import OIDCProxy

    return OIDCProxy(
        config_url=f"{issuer_url}/.well-known/openid-configuration",
        client_id=os.environ["MCP_OIDC_CLIENT_ID"],
        client_secret=os.environ["MCP_OIDC_CLIENT_SECRET"],
        base_url=os.environ["MCP_BASE_URL"],
        # Must match the mcp-server redirectURIs entry in config/dex/start-dex.sh.
        redirect_path="/oauth/callback",
        required_scopes=["openid", "profile", "email"],
        # Dex's access token carries no scope claim (it's an opaque-style
        # token; the real JWT is the id_token), so validate that instead of
        # the access_token. Requires fastmcp>=3.0.1.
        verify_id_token=True,
    )


SERVER_INSTRUCTIONS = (
    # Describe tools by role, not by their bare MCP names: chat clients
    # register them under prefixed/suffixed names (e.g. search_satellites_mcp_tatc),
    # and models that follow these instructions literally call the bare name,
    # which fails with "tool not found".
    "Use the satellite search tool when a satellite name is broad or ambiguous. "
    "Use the satellite info tool for metadata and current TLE data. "
    "Use the ground-track tool only after resolving an exact satellite name or "
    "NORAD ID; its times are UTC and its position altitude is in meters. "
    "Render TLE lines in a fenced code block so they are not wrapped. "
    "All tools are read-only."
)

mcp = FastMCP(
    "tatc-ai-mcp-server",
    instructions=SERVER_INSTRUCTIONS,
    auth=_build_auth(),
)


# Time unit normalization mapping
_TIME_UNITS = {
    "second": "seconds",
    "sec": "seconds",
    "secs": "seconds",
    "minute": "minutes",
    "min": "minutes",
    "mins": "minutes",
    "hour": "hours",
    "hr": "hours",
    "hrs": "hours",
    "day": "days",
}

_UNIT_TO_DELTA = {
    "seconds": lambda amount: timedelta(seconds=amount),
    "minutes": lambda amount: timedelta(minutes=amount),
    "hours": lambda amount: timedelta(hours=amount),
    "days": lambda amount: timedelta(days=amount),
}

_WORD_NUMBERS = {
    "a": 1,
    "an": 1,
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}


def _utcnow_naive() -> datetime:
    """Return the current UTC time as a naive datetime for TAT-C compatibility."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_time_unit(unit: str) -> Optional[str]:
    """Normalize a time unit string."""
    normalized = unit.lower()
    return _TIME_UNITS.get(
        normalized, normalized if normalized in _UNIT_TO_DELTA else None
    )


def _unit_to_timedelta(unit: str, amount: float) -> timedelta:
    """Convert normalized unit and amount to timedelta."""
    converter = _UNIT_TO_DELTA.get(unit)
    if not converter:
        raise ValueError(f"Unknown time unit: {unit}")
    return converter(amount)


def _parse_amount_phrase(amount_str: str) -> float:
    """Parse a numeric or simple word-number amount."""
    normalized = amount_str.strip().lower().replace("-", " ")
    if not normalized:
        raise ValueError("Amount is required")

    try:
        return float(normalized)
    except ValueError:
        pass

    total = 0
    for token in normalized.split():
        if token not in _WORD_NUMBERS:
            raise ValueError(f"Unknown amount token: {token}")
        total += _WORD_NUMBERS[token]

    return float(total)


def _parse_relative_time(time_str: str) -> Optional[datetime]:
    """Parse relative time expressions like 'in 1 hour' or 'in one hour'."""
    if not time_str.startswith("in "):
        return None

    try:
        parts = time_str[3:].split()
        if len(parts) < 2:
            return None

        amount = _parse_amount_phrase(" ".join(parts[:-1]))
        unit = _parse_time_unit(parts[-1])
        if not unit:
            return None

        return _utcnow_naive() + _unit_to_timedelta(unit, amount)
    except (ValueError, IndexError):
        return None


def parse_time_input(time_str: str) -> datetime:
    """Parse a time input string to a naive UTC datetime.

    Supports ISO-8601 format, "now", "current", and relative expressions like
    "in 1 hour" or "in one hour".
    """
    if not isinstance(time_str, str):
        raise ValueError(f"Time must be a string, got {type(time_str)}")
    normalized = time_str.strip().lower()

    if normalized in ("now", "current"):
        return _utcnow_naive()

    relative = _parse_relative_time(normalized)
    if relative:
        return relative

    try:
        dt = date_parser.parse(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.replace(tzinfo=None)
    except Exception as exc:
        raise ValueError(f"Could not parse time string '{time_str}': {exc}") from exc


def parse_duration(duration_str: str) -> timedelta:
    """Parse a duration string such as "1 hour", "one hour", or "60 minutes".

    A bare number with no unit means minutes.
    """
    if not isinstance(duration_str, str):
        raise ValueError(f"Duration must be a string, got {type(duration_str)}")
    normalized = duration_str.strip().lower()

    try:
        return timedelta(minutes=float(normalized))
    except ValueError:
        pass

    try:
        parts = normalized.split()
        if len(parts) < 2:
            raise ValueError("Duration must include a unit")

        amount = _parse_amount_phrase(" ".join(parts[:-1]))
        unit = _parse_time_unit(parts[-1])
        if not unit:
            raise ValueError(f"Unknown time unit: {parts[-1]}")

        return _unit_to_timedelta(unit, amount)
    except (ValueError, IndexError) as exc:
        raise ValueError(
            f"Could not parse duration string '{duration_str}': {exc}"
        ) from exc


# Tool handlers. Sync functions on purpose: FastMCP runs them in a worker
# thread, so the blocking CelesTrak HTTP calls and TAT-C propagation do not
# block the event loop.


def _generate_ground_track(
    satellite_identifier: str,
    start_time: Optional[str] = None,
    duration: Optional[str] = None,
    step_interval: Optional[str] = None,
    include_footprint: bool = False,
) -> List[Dict[str, Any]]:
    """Generate ground track telemetry for a satellite."""
    start_time_dt = (
        _utcnow_naive() if start_time is None else parse_time_input(start_time)
    )
    duration_delta = (
        timedelta(hours=1) if duration is None else parse_duration(duration)
    )
    step_seconds = (
        60.0 if step_interval is None else parse_duration(step_interval).total_seconds()
    )

    end_time_dt = start_time_dt + duration_delta
    start_time_dt, end_time_dt = validate_time_range(start_time_dt, end_time_dt)
    step_seconds = validate_step_interval(step_seconds)
    validate_ground_track_sample_count(start_time_dt, end_time_dt, step_seconds)

    sat_info = celestrak_client.get_satellite_info(satellite_identifier)
    satellite = create_satellite_from_tle(sat_info["tle_line1"], sat_info["tle_line2"])
    ground_track = compute_ground_track(
        satellite, start_time_dt, end_time_dt, step_seconds
    )
    # Footprints are 86%+ of the payload and models rendering tables never
    # need them, so they are opt-in. Skipping also saves the compute.
    footprints = None
    if include_footprint:
        footprints = [
            calculate_footprint_from_position(lat_deg, lon_deg, alt_m)
            for _, lat_deg, lon_deg, alt_m in ground_track
        ]

    return format_ground_track_response(
        str(sat_info["norad_id"]), ground_track, footprints
    )


@mcp.tool
def generate_ground_track(
    satellite_identifier: str,
    start_time: Optional[str] = None,
    duration: Optional[str] = None,
    step_interval: Optional[str] = None,
    include_footprint: bool = False,
) -> List[Dict[str, Any]]:
    """Generate a satellite ground track over a time period.

    Args:
        satellite_identifier: Satellite name (e.g., "ISS", "Hubble") or NORAD ID.
        start_time: Start time as ISO-8601, "now", or relative like "in one hour".
            Defaults to "now".
        duration: How long to generate, e.g. "1 hour" or "60 minutes".
            Defaults to 1 hour. Maximum 30 days and 2000 output points.
        step_interval: Time step between points, e.g. "30 sec" or "1 minute".
            Defaults to 1 minute. Between 1 second and 1 hour.
        include_footprint: Include the footprint_geojson visibility polygon
            per point. Defaults to False: footprints are ~86% of the payload
            and tabular answers never need them. Set True for mapping.

    Returns:
        Telemetry objects with id, time (ISO-8601 UTC), position_lla
        (lat_deg, lon_deg, alt_m), and optional footprint_geojson.
    """
    return _generate_ground_track(
        satellite_identifier, start_time, duration, step_interval,
        include_footprint,
    )


@mcp.tool
def get_satellite_info(satellite_identifier: str) -> Dict[str, Any]:
    """Get satellite metadata and current TLE data from CelesTrak.

    Args:
        satellite_identifier: Satellite name (e.g., "ISS") or NORAD ID.

    Returns:
        Object with norad_id, name, tle_line1, and tle_line2.
    """
    return celestrak_client.get_satellite_info(satellite_identifier)


@mcp.tool
def search_satellites(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search currently orbiting satellites by name in the CelesTrak database.

    Decayed objects are excluded so returned names and NORAD IDs work with
    the other tools. Use this first when the exact name is unknown.

    Args:
        query: Satellite name or partial name (e.g., "Starlink", "GPS").
        limit: Maximum number of results, between 1 and 50. Defaults to 10.

    Returns:
        Objects with norad_id, name, object_type, country, and launch_date.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer between 1 and 50")
    return celestrak_client.search_satellites_by_name(query, limit=limit)


def main() -> None:
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8000,
        # Behind Traefik, uvicorn otherwise only trusts X-Forwarded-* from
        # 127.0.0.1: it would see every request as http on the container's
        # internal address, mismatching the https public URL OIDCProxy uses
        # for token audiences, and rejecting every token as invalid.
        uvicorn_config={"proxy_headers": True, "forwarded_allow_ips": "*"},
    )


if __name__ == "__main__":
    main()
