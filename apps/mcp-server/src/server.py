"""TAT-C MCP server: satellite tools behind the FastMCP stack used by tatc-ai."""

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from dateutil import parser as date_parser
from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from mcp.types import EmbeddedResource, TextContent, TextResourceContents

from . import celestrak_client, oscar_client
from .generic_coverage import get_access_cells as _access_cells
from .generic_coverage import get_access_daily as _access_daily
from .generic_coverage import get_access_revisit as _access_revisit
from .generic_coverage import begin_access_run as _begin_run
from .generic_coverage import finish_access_run as _finish_run
from .generic_coverage import set_access_region as _set_region
from .geometry import (
    GeometryAssessment,
    convert_swath_to_for,
    extract_geometry_candidates,
    numeric_certainty,
)
from .map_view import build_ground_track_map_html
from .schema_formatter import format_ground_track_response
from .static_map import render_static_map_png
from .tatc_integration import (
    create_orbit_from_omm,
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
    "Use the satellite info tool for metadata and current GP (OMM) elements. "
    "Use the ground-track tool only after resolving an exact satellite name or "
    "NORAD ID; its times are UTC and its position altitude is in meters. "
    "Use the instrument search tool to find sensors by name, acronym, or type. "
    "Use the satellite-instruments tool when you need to compare the payloads "
    "on a satellite. "
    "Use the instrument info tool for an instrument's measurement specs and "
    "carrying satellites; its spec values are source text, not parsed numbers. "
    "The geometry resolver returns source context and numeric candidates; it "
    "does not assess compatibility. Read the complete instrument context, identify "
    "the source value and mode for this study, and assess whether the coverage "
    "model represents that geometry. Mark geometry supported only when the "
    "source evidence establishes continuous nadir-centered coverage without "
    "gaps or separate pointing. For separated tracks with a documented overall "
    "span, use that span only as an approximate filled envelope and warn that "
    "gaps count as accessible in the model. Do not present it as continuous "
    "coverage. For any known mismatch, provide status approximate and a specific "
    "warning about what the model simplifies. Approximate runs automatically "
    "without confirmation, and the warning remains with summaries and fetched "
    "views. Before any run, ask the researcher to resolve an applicable mode or "
    "value left unclear by the source, insufficient evidence, or conflicting "
    "source facts. Never choose a typical, widest, or best-case option for the "
    "researcher. Keep source_text faithful to returned evidence; never add a "
    "selected value or mode to make it appear sourced. Supply a "
    "geometry_assessment with relevant source_text and an "
    "explanation to the finish tool. Its checks validate the structure and "
    "numeric value, not the scientific interpretation or source authenticity. "
    "A citation alone is not evidence. Do not fetch arbitrary reference URLs. "
    "For coverage, call the begin tool, then the region tool, then the finish "
    "tool, then the fetch tools by result ID for daily rows, coverage cells "
    "as GeoJSON, or "
    "revisit numbers. The begin tool takes several satellites separated by "
    "commas to model a constellation. Set daylight_only on the finish tool for "
    "optical imagers. "
    "Coverage is modeled access at each grid-cell center only: never claim "
    "every point of the region was scanned or that an acquisition occurred. "
    "A daily gap_area_km2 of 0 means every cell center had access that UTC "
    "day, not continuous observation. "
    "Talk to the researcher like a companion, in plain sentences with every "
    "number that matters: the geometry value and its source, the metrics, "
    "the window. Show the result ID once, since follow-up questions need it. "
    "Never show draft IDs, function names, call syntax, or raw payloads, and "
    "never narrate your tool steps. Ask for missing pieces conversationally, "
    "like a place and dates. "
    "When the user asks to visualize, map, plot, or show a ground track, use "
    "the ground track map tool with the satellite, start time, duration, and "
    "step interval you would give the ground-track tool. "
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


def _compute_ground_track(
    satellite_identifier: str,
    start_time: Optional[str] = None,
    duration: Optional[str] = None,
    step_interval: Optional[str] = None,
    include_footprint: bool = False,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Return the satellite info and its ground track telemetry."""
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
    satellite = create_orbit_from_omm(sat_info["omm"])
    # Footprints are 86%+ of the payload and models rendering tables never
    # need them, so they are opt-in. Skipping also saves the compute.
    ground_track = compute_ground_track(
        satellite, start_time_dt, end_time_dt, step_seconds,
        include_footprint=include_footprint,
    )
    messages = format_ground_track_response(str(sat_info["norad_id"]), ground_track)
    return sat_info, messages


@mcp.tool
def generate_ground_track(
    satellite_identifier: str,
    start_time: Optional[str] = None,
    duration: Optional[str] = None,
    step_interval: Optional[str] = None,
    include_footprint: bool = False,
) -> List[Dict[str, Any]]:
    """Generate a satellite ground track over a time period. To show it on an
    interactive map, use the ground track map tool instead.

    Args:
        satellite_identifier: Satellite name (e.g., "ISS", "Hubble") or NORAD ID.
        start_time: Start time as ISO-8601, "now", or relative like "in one hour".
            Defaults to "now".
        duration: How long to generate, e.g. "1 hour" or "60 minutes".
            Defaults to 1 hour. Maximum 30 days and 2000 output points.
        step_interval: Time step between points, e.g. "30 sec" or "1 minute".
            Defaults to 1 minute. Between 1 second and 1 hour.
        include_footprint: Include footprint_geojson per point: the ground
            area inside a 60 degree cone under the satellite, from TAT-C, as
            a Polygon or a MultiPolygon split at the antimeridian. Defaults to
            False: footprints are ~86% of the payload and tabular answers never
            need them.

    Returns:
        Telemetry objects with id, time (ISO-8601 UTC), position_lla
        (lat_deg, lon_deg, alt_m), and optional footprint_geojson.
    """
    _, messages = _compute_ground_track(
        satellite_identifier, start_time, duration, step_interval,
        include_footprint,
    )
    return messages


@mcp.tool
def ground_track_map(
    satellite_identifier: str,
    start_time: Optional[str] = None,
    duration: Optional[str] = None,
    step_interval: Optional[str] = None,
) -> ToolResult:
    """Visualize, map, or plot a satellite ground track on an interactive map.
    Use this whenever the user asks to see, show, draw, or visualize where a
    satellite goes. The map always includes TAT-C footprints, which the user
    can toggle.

    Args:
        satellite_identifier: Satellite name (e.g., "ISS", "Hubble") or NORAD ID.
        start_time: Start time as ISO-8601, "now", or relative like "in one hour".
            Defaults to "now".
        duration: How long to generate, e.g. "1 hour" or "60 minutes".
            Defaults to 1 hour. Maximum 30 days and 2000 output points.
        step_interval: Time step between points, e.g. "30 sec" or "1 minute".
            Defaults to 1 minute. Between 1 second and 1 hour.

    Returns:
        A short text summary and the map (interactive and static views),
        which is shown to the user.
    """
    # Footprints stay in the map page; they never reach the model.
    sat_info, messages = _compute_ground_track(
        satellite_identifier, start_time, duration, step_interval,
        include_footprint=True,
    )
    norad_id = str(sat_info["norad_id"])
    name = sat_info.get("name", norad_id)
    summary = (
        f"Showing an interactive ground track map of {name} (NORAD {norad_id}), "
        f"{messages[0]['time']} to {messages[-1]['time']}, {len(messages)} points. "
        "Place the map's UI marker in your reply; do not draw an ASCII map."
    )
    png = render_static_map_png(f"{name} (NORAD {norad_id})", messages)
    html = build_ground_track_map_html(name, norad_id, messages, png)
    return ToolResult(content=[
        TextContent(type="text", text=summary),
        EmbeddedResource(type="resource", resource=TextResourceContents(
            uri=f"ui://tatc/ground-track/{norad_id}/{messages[0]['time']}",
            mimeType="text/html",
            text=html,
        )),
    ])


@mcp.tool
def get_satellite_info(satellite_identifier: str) -> Dict[str, Any]:
    """Get satellite metadata and current GP (OMM) elements from CelesTrak.

    Args:
        satellite_identifier: Satellite name (e.g., "ISS") or NORAD ID.

    Returns:
        Object with norad_id, name, and omm (a list of OMM element records).
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
        An empty list means no matches; upstream failures are reported as errors.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer between 1 and 50")
    return celestrak_client.search_satellites_by_name(query, limit=limit)


@mcp.tool
def search_instruments(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search the WMO OSCAR database for Earth observation instruments.

    Args:
        query: Instrument name, acronym, or partial name (e.g., "ABI", "MODIS").
        limit: Maximum number of results, between 1 and 50. Defaults to 10.

    Returns:
        Objects with slug, label, and oscar_url. Follow oscar_url for the
        full OSCAR record, or use the satellite-instruments tool for a
        satellite's instrument list with status and latency fields.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer between 1 and 50")
    return oscar_client.search_instruments(query, limit=limit)


@mcp.tool
def get_satellite_instruments(satellite_identifier: str) -> Dict[str, Any]:
    """Get the instruments on a satellite, with OSCAR mission metadata.

    Use this to build the instrument model input for observation or
    coverage analysis. Resolves names and acronyms against the full OSCAR
    satellite list; unknown or ambiguous names return an error.

    Args:
        satellite_identifier: Satellite acronym, name, or OSCAR slug.

    Returns:
        Satellite metadata (agency, status, orbit, altitude, launch and
        end-of-life dates, oscar_url) plus instruments with per-satellite
        status, start date, end of life, and data latency.
    """
    return oscar_client.resolve_satellite(satellite_identifier)


@mcp.tool
def get_instrument_info(instrument_identifier: str) -> Dict[str, Any]:
    """Get an OSCAR instrument record with measurement specs.

    Resolves an instrument by acronym, name, or OSCAR slug. Spec fields
    such as Resolution, Coverage / Cycle, and Scanning Technique are source
    text from the OSCAR instrument page, not parsed numbers.

    Args:
        instrument_identifier: Instrument acronym, name, or OSCAR slug.

    Returns:
        Instrument identity, classification, measured variables, carrying
        satellites with dates and latency, spec fields, and oscar_url.
    """
    return oscar_client.get_instrument(instrument_identifier)


@mcp.tool
def resolve_instrument_geometry(instrument_identifier: str) -> Dict[str, Any]:
    """Extract numeric candidates with provenance for assistant assessment.

    Resolves an instrument by acronym, name, or OSCAR slug, then parses
    numeric values from its specs and instrument descriptions. This tool
    does not assess compatibility; read all returned context and provide a
    geometry_assessment when finishing a run.

    Args:
        instrument_identifier: Instrument acronym, name, or OSCAR slug.

    Returns:
        Complete source context, numeric candidates with provenance, and
        numeric certainty. No compatibility status is inferred.
    """
    record = oscar_client.get_instrument(instrument_identifier)
    specs = record.get("specs") if isinstance(record, dict) else None
    candidate_sources = dict(specs) if isinstance(specs, dict) else {}
    for key, label in (
        ("api_description", "API description"),
        ("page_description", "Instrument page description"),
        ("description", "Instrument description"),
    ):
        description = record.get(key)
        if key == "description" and any(
            description == record.get(source_key)
            for source_key in ("api_description", "page_description")
        ):
            continue
        if isinstance(description, str) and description.strip():
            candidate_sources[label] = description
    candidates = extract_geometry_candidates(candidate_sources)
    return {
        "source_context": record,
        "geometry_candidates": candidates,
        "numeric_certainty": numeric_certainty(candidates),
        "next_step": (
            "Read the complete source_context, choose the value and mode for "
            "this study, and provide geometry_assessment to the finish tool. "
            "Ask the researcher first if the evidence is unclear or conflicting."
        ),
    }


@mcp.tool
def convert_swath_to_field_of_regard(
    swath_width_km: float,
    orbit_altitude_m: float,
) -> Dict[str, Any]:
    """Convert a ground swath width at an altitude to a field of regard.

    Uses the same TAT-C math the generic coverage tool uses, so the
    number matches the later run. Both inputs are flat numbers.

    Args:
        swath_width_km: Ground swath width in km, positive and finite.
        orbit_altitude_m: Orbit altitude in meters, positive and finite.

    Returns:
        Field of regard in degrees with the inputs echoed back.
    """
    return {
        "field_of_regard_deg": convert_swath_to_for(swath_width_km, orbit_altitude_m),
        "swath_width_km": float(swath_width_km),
        "orbit_altitude_m": float(orbit_altitude_m),
    }


@mcp.tool
def begin_access_run(
    satellite_identifier: str,
    start_time: str,
    end_time: Optional[str] = None,
) -> Dict[str, Any]:
    """Lock the satellites and UTC window for an access run.

    Takes one satellite, or up to 6 separated by commas to model them
    together as a constellation, e.g. "Sentinel-2A, Sentinel-2B".
    Validates the inputs against current CelesTrak elements and returns
    a draft ID with each satellite's orbit mean altitude. Set the region with the
    region tool, then finish with grid size, geometry, and source. Start
    must be an explicit UTC ISO-8601 value ending in Z or using +00:00.
    End is the same format and defaults to 24 hours after the start when
    omitted. The window is at most 366 days.

    Args:
        satellite_identifier: Satellite name or NORAD ID for CelesTrak, or
            up to 6 of them separated by commas.
        start_time: Inclusive UTC start, e.g. "2026-09-18T04:00:00Z".
        end_time: Exclusive UTC end, later than start_time. Defaults to
            24 hours after start_time when omitted.

    Returns:
        Draft ID, satellites with OMM epoch and mean altitude, window,
        and the next step. No region or coverage numbers yet.
    """
    return _begin_run(satellite_identifier, start_time, end_time)


@mcp.tool
def set_access_region(
    draft_id: str,
    west_lon: float,
    south_lat: float,
    east_lon: float,
    north_lat: float,
) -> Dict[str, Any]:
    """Set the region on a staged access draft.

    Takes four separate numbers in west, south, east, north order; do
    not nest them in a list. Longitude boxes must not cross the
    antimeridian and must be narrower than 180 degrees. Returns the
    draft with its region and the next step.

    Args:
        draft_id: Draft ID from the begin tool.
        west_lon: Bounding-box west longitude, from -180 through less than 180.
        south_lat: Bounding-box south latitude, from -90 through less than 90.
        east_lon: Bounding-box east longitude, greater than west and at most 180.
        north_lat: Bounding-box north latitude, greater than south and at most 90.

    Returns:
        Draft ID, satellite, window, region, and the next step.
    """
    return _set_region(
        draft_id, [west_lon, south_lat, east_lon, north_lat]
    )


@mcp.tool
def finish_access_run(
    draft_id: str,
    grid_rows: int = 6,
    grid_columns: int = 6,
    field_of_regard_deg: Optional[float] = None,
    swath_width_km: Optional[float] = None,
    geometry_reference: Optional[str] = None,
    instrument_name: Optional[str] = None,
    daylight_only: bool = False,
    *,
    geometry_assessment: GeometryAssessment,
) -> Dict[str, Any]:
    """Run a staged access analysis and save it under a result ID.

    Takes a draft ID with its region already set, plus grid size, exactly
    one of field of regard or swath width, a citation, and a structured
    geometry_assessment. The assistant supplies its interpretation of the
    complete source context. The server checks that the source_text includes
    the selected number, but does not verify the interpretation. Understood
    approximations run automatically and return their warning. Unclear or
    conflicting evidence must be clarified first. Do not fetch the reference
    URL. For a constellation, the same geometry applies to every satellite.
    Set daylight_only
    for optical imagers so night passes do not count. Works for any staged
    window up to 366 days. A swath is converted at each satellite's orbit
    altitude with TAT-C math. Modeled access does not confirm an acquisition.

    Args:
        draft_id: Draft ID from the begin tool.
        grid_rows: Number of equal latitude partitions, 1 through 12.
        grid_columns: Number of equal longitude partitions, 1 through 12.
        field_of_regard_deg: Full cross-track angle in degrees, 0 to 360.
        swath_width_km: Ground swath width in km as an alternative.
        geometry_reference: Source for the angle or swath, required.
        instrument_name: Label for the modeled scan. Defaults to modeled scan.
        daylight_only: Keep only accesses with the sun up at the cell
            center, for optical imagers. Defaults to false.
        geometry_assessment: Status, relevant source_text, explanation,
            optional warning (required for approximate), and selected mode.

    Returns:
        Result ID, compact summary counts, metrics, geometry provenance,
        and evidence flags. Daily rows and cells are saved, not returned.
    """
    return _finish_run(
        draft_id,
        grid_rows=grid_rows,
        grid_columns=grid_columns,
        field_of_regard_deg=field_of_regard_deg,
        swath_width_km=swath_width_km,
        geometry_reference=geometry_reference,
        instrument_name=instrument_name,
        daylight_only=daylight_only,
        geometry_assessment=geometry_assessment,
    )


@mcp.tool
def get_access_daily(result_id: str, offset: int = 0, limit: int = 31) -> Dict[str, Any]:
    """Fetch saved daily observed and gap areas by result ID.

    Pages through the daily rows of a saved access run without
    re-running the analysis. Offset starts at 0 and limit is 1 to 366.

    Args:
        result_id: Result ID from a saved access run.
        offset: Number of daily rows to skip, 0 or more.
        limit: Number of daily rows to return, 1 through 366.

    Returns:
        Daily rows with observed and gap areas and cell IDs, plus the
        geometry status and warning, interpretation, and access evidence.
    """
    return _access_daily(result_id, offset, limit)


@mcp.tool
def get_access_cells(result_id: str, include: str = "all") -> Dict[str, Any]:
    """Fetch the saved grid map by result ID as GeoJSON.

    Returns grid cells as a GeoJSON feature collection, each cell marked
    observed or gap for the saved window with its access count, mean
    access minutes, and mean revisit hours. The collection metadata and
    feature properties retain geometry status and warning. Filter
    with all, observed, or gaps. Modeled access does not confirm an acquisition.

    Args:
        result_id: Result ID from a saved access run.
        include: Which cells to return: all, observed, or gaps.

    Returns:
        GeoJSON collection with per-cell status, geometry metadata, and access evidence.
    """
    return _access_cells(result_id, include)


@mcp.tool
def get_access_revisit(result_id: str) -> Dict[str, Any]:
    """Fetch saved revisit numbers by result ID.

    Returns mean, median, and max revisit hours, mean access minutes,
    and the longest unobserved gap with their definitions, without
    re-running the analysis. Modeled access does not confirm an
    acquisition.

    Args:
        result_id: Result ID from a saved access run.

    Returns:
        Revisit metrics with definitions, geometry status and warning,
        interpretation, and access evidence.
    """
    return _access_revisit(result_id)


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
