"""TAT-C MCP server: satellite tools behind the FastMCP stack used by tatc-ai."""

import os
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from uuid import UUID, uuid4
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple

from dateutil import parser as date_parser
from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from mcp.types import EmbeddedResource, TextContent, TextResourceContents
from pydantic import Field

from . import celestrak_client
from .coverage_analysis import (
    CoverageRequest,
    json_string_or,
    analyze as compute_coverage_analysis,
    sample_orbit,
    timestamp,
)
from .coverage_view import build_viewer_url, register_viewer_routes
from .research_data import ResearchData
from .map_view import build_ground_track_map_html
from .schema_formatter import format_ground_track_response
from .static_map import render_static_map_png
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
    "Choose tools and output from the research question and conversation context, not trigger words. "
    "A request to visualize can mean a chart, a table, a timeline, a geographic map, or an orbital simulation. "
    "Do not treat every visualization request as a Cesium scene. Use supplied context to choose a useful "
    "presentation; ask a focused question only when missing inputs or competing interpretations would "
    "materially change the result. Do not ask the user to write tool names or special prompt syntax. "
    "Use the satellite search tool to resolve ambiguous identities, the satellite info tool for metadata/TLEs, ground-track "
    "computation for trajectory data, and the ground track map tool for an explicit ground-track map. "
    "Use coverage analysis for numerical access/revisit questions about supplied mission designs. "
    "Use the research visualization tool when a visual or interactive study is requested or clearly implied: "
    "coverage comparisons as overview charts, access/gap timing as timelines, exact values as tables, "
    "geography as map2d, and orbital motion or spatial geometry as simulation3d. "
    "The data view renders values from an earlier analysis or supplied dataset without rerunning physics. "
    "The orbit view can also show altitude charts and sampled-position tables without loading a globe. "
    "Informational questions, TLE text, requests to write plotting code, and numerical answers do not "
    "automatically request an interactive scene. Requests for code are answered with code only; do not call any tool. "
    "Preserve requested time windows and scope; explain tool limits and ask before shortening or reducing inputs. "
    "Coverage requires satellites, instruments and study locations supplied or agreed in this conversation. An orbit alone is not sensor coverage. "
    "Use the built-in example only when the user requests an example. "
    "Answer follow-up questions about an earlier study from the exact values in its tool result, and re-show "
    "it in another presentation with the visualize call that result states; never rebuild it from memory. "
    "For UI-originated rerun_tatc_study intents, call the registered visualization tool using the exact params "
    "provided, preserving workspace_id so the study pane receives the new result. Do not substitute the example. "
    "Use registered tool names as exposed by the client. "
    "Coverage gaps at the window boundaries are not complete revisits; interpolated display samples are not "
    "the event analysis resolution. Only supplied points are evaluated, not the area between them. "
    "Use only values the user supplied or tools returned in this conversation; never invent satellites, TLEs, "
    "orbits, sensor field of regard, locations, dates or metrics. Place names such as Phoenix are study points "
    "or ground stations, not satellites. "
    "Swath rendering, footprints of basic field-of-regard instruments, solar-illumination masks, "
    "observation scheduling, downlink latency, area heatmaps, cost models and design-space optimization "
    "are not supported: say so plainly, offer what is supported (point coverage studies with their table "
    "and CSV export, and footprints of pointed instruments), and never suggest tool calls with "
    "placeholder inputs. Never write tool-call syntax such as <function=...> in a reply; call the tool or answer "
    "in prose. After an error that user-supplied values cannot fix, stop and ask the user. "
    "All tools are read-only."
)

mcp = FastMCP(
    "tatc-ai-mcp-server",
    instructions=SERVER_INSTRUCTIONS,
    auth=_build_auth(),
)


register_viewer_routes(mcp)

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

    messages = format_ground_track_response(
        str(sat_info["norad_id"]), ground_track, footprints
    )
    return sat_info, messages


def _generate_ground_track(
    satellite_identifier: str,
    start_time: Optional[str] = None,
    duration: Optional[str] = None,
    step_interval: Optional[str] = None,
    include_footprint: bool = False,
) -> List[Dict[str, Any]]:
    """Generate ground track telemetry for a satellite."""
    _, messages = _compute_ground_track(
        satellite_identifier,
        start_time,
        duration,
        step_interval,
        include_footprint,
    )
    return messages


@mcp.tool
def generate_ground_track(
    satellite_identifier: str,
    start_time: Optional[str] = None,
    duration: Optional[str] = None,
    step_interval: Optional[str] = None,
    include_footprint: bool = False,
) -> List[Dict[str, Any]]:
    """Generate a satellite ground track over a time period. To show it on an
    interactive map, use the ground track map tool with the same arguments.

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
            Dateline-crossing footprints may use GeoJSON MultiPolygon geometry.

    Returns:
        Telemetry objects with id, time (ISO-8601 UTC), position_lla
        (lat_deg, lon_deg, alt_m), and optional footprint_geojson.
    """
    return _generate_ground_track(
        satellite_identifier,
        start_time,
        duration,
        step_interval,
        include_footprint,
    )


@mcp.tool
def ground_track_map(
    satellite_identifier: str,
    start_time: Optional[str] = None,
    duration: Optional[str] = None,
    step_interval: Optional[str] = None,
    include_footprint: bool = True,
) -> ToolResult:
    """Visualize, map, or plot a satellite ground track on an interactive map.
    Use when the request implies a geographic visual, not for metadata, TLEs,
    tables, or numerical questions. For a 3D orbit replay use the visualize tool with the orbit view.
    Takes the same arguments as the ground-track tool.

    Args:
        satellite_identifier: Satellite name (e.g., "ISS", "Hubble") or NORAD ID.
        start_time: Start time as ISO-8601, "now", or relative like "in one hour".
            Defaults to "now".
        duration: How long to generate, e.g. "1 hour" or "60 minutes".
            Defaults to 1 hour. Maximum 30 days and 2000 output points.
        step_interval: Time step between points, e.g. "30 sec" or "1 minute".
            Defaults to 1 minute. Between 1 second and 1 hour.
        include_footprint: Let the user show the visibility footprint on the
            map. Defaults to True; footprints stay in the map, not the reply.

    Returns:
        A short text summary and the map (interactive and static views),
        which is shown to the user.
    """
    sat_info, messages = _compute_ground_track(
        satellite_identifier,
        start_time,
        duration,
        step_interval,
        include_footprint,
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
    return ToolResult(
        content=[
            TextContent(type="text", text=summary),
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(
                    uri=f"ui://tatc/ground-track/{norad_id}/{messages[0]['time']}",
                    mimeType="text/html",
                    text=html,
                ),
            ),
        ]
    )


@mcp.tool
def visualize(
    view: Literal["orbit", "coverage", "example", "data"],
    satellite_identifiers: Optional[list[str]] = None,
    scenario: Optional[json_string_or(CoverageRequest)] = None,
    dataset: Optional[json_string_or(ResearchData)] = None,
    presentation: Optional[
        Literal["overview", "timeline", "table", "map2d", "simulation3d"]
    ] = None,
    analysis_id: Optional[str] = None,
    # Plain strings and ints: LibreChat hides schema errors from the model, so
    # these are validated below with messages it can act on.
    workspace_id: Optional[str] = None,
    start_time: Optional[str] = None,
    duration: Optional[str] = None,
    display_step_seconds: Optional[int] = None,
) -> ToolResult:
    """Create an interactive research workspace; choose the output for the question.

    This is not a keyword-triggered simulation tool. Use only when a visual or
    interactive study is wanted. Do not call it for requests to write code, or
    for text and numbers. Use conversation context; clarify only missing facts
    that affect the result. A place and date alone are not a coverage study: ask
    which satellites and sensors to use. Never invent measurements or sensor assumptions.
    Not supported, so explain instead of calling: swath rendering, footprints of
    basic field-of-regard instruments, solar-illumination masks, observation
    scheduling/tasking, ground-station downlink/latency, area/regional heatmaps,
    cost models or design-space optimization. Never invent TLEs, orbits or field of regard to approximate them.

    presentation selects the initial view: overview for comparisons/charts,
    timeline for observation and gap timing, table for exact values, map2d for
    geographic context, simulation3d for orbital motion/spatial geometry. Charts
    and tables do not load Cesium. All views share the same study pane and data.

    view='data': dataset contains named numeric series aligned to labels, a common
    unit, kind ('line' for a requested line chart, else 'bar') and provenance. Only
    values the user typed (provenance kind 'user') or metrics of a coverage study
    (kind 'study' with its analysis_id; labels are its point names) may be charted;
    study values are checked. Source attribution is optional;
    omit it when the prompt directly supplies the values. Use one data call for
    such plots; no satellite lookup or coverage calculation is needed. Supports
    overview (a labelled chart with axes) or table. Never invent values or sources.

    view='coverage': supply the complete agreed scenario: TATC satellites with
    explicit instruments (circular orbit fields: type, mean_altitude, inclination,
    right_ascension_ascending_node, true_anomaly, epoch), study-point coordinates,
    UTC start/end, and optional display_step_seconds. Supports all presentations; defaults to overview.
    Missing mission or sensor inputs require clarification. Study-point coverage
    is not regional coverage. Use analyze_coverage for numerical results alone.
    To re-show an earlier study, pass its analysis_id instead of scenario.

    view='orbit': supply 1–4 resolved satellite identifiers. Optional start_time
    defaults to now, duration to '1 hour', sampling to 60 seconds; report defaults.
    Uses current CelesTrak TLEs. Supports altitude overview, sampled-position table,
    map2d and simulation3d (default); no sensor or coverage is inferred.

    view='example': explicitly requested hypothetical coverage example, with no
    custom mission inputs; presentation may be selected. Never substitute it for
    a user scenario. All study windows are at most 24 hours/6000 display samples.
    Explain limits and ask before changing the requested scope.
    workspace_id is only for UI rerun intents, copied from their params; omit it otherwise.
    """
    if workspace_id is not None:
        try:
            workspace_id = str(UUID(workspace_id))
        except ValueError:
            raise ValueError(
                f"workspace_id {workspace_id!r} is not a workspace UUID. It is only "
                "copied from UI rerun intent params; it is not the UI marker ID. "
                "Call again without workspace_id."
            ) from None
    if analysis_id is not None and view != "coverage":
        raise ValueError(
            "analysis_id re-shows an earlier study with view='coverage'. "
            "For the built-in example use view='example' without analysis_id"
        )
    ui = {
        "presentation": presentation
        or ("simulation3d" if view == "orbit" else "overview"),
        "workspace_id": workspace_id or str(uuid4()),
        "view": view,
    }
    if view == "data":
        if (
            dataset is None
            or scenario is not None
            or any(
                x is not None
                for x in (
                    satellite_identifiers,
                    start_time,
                    duration,
                    display_step_seconds,
                )
            )
        ):
            raise ValueError(
                "The data view requires dataset and no mission/orbit inputs"
            )
        if ui["presentation"] not in ("overview", "table"):
            raise ValueError(
                "Numerical datasets support overview charts or tables; a geographic scene requires orbit or coverage inputs"
            )
        if dataset.provenance.kind == "study":
            _check_study_values(dataset)
        data = dataset.model_dump(mode="json")
        result = {"dataset": data, "ui": ui}
        return ToolResult(
            content=[
                TextContent(
                    type="text",
                    text=(
                        f"Research workspace ready: {dataset.title!r} as a {dataset.kind} "
                        f"{'table' if ui['presentation'] == 'table' else 'chart'}. "
                        "Place its UI marker in the reply; the user can open the study pane."
                    ),
                ),
                EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=f"ui://tatc/data/{uuid4()}",
                        mimeType="text/uri-list",
                        text=build_viewer_url(result),
                    ),
                ),
            ],
            structured_content={
                "title": dataset.title,
                "source": dataset.source,
                "presentation": ui["presentation"],
            },
        )
    if dataset is not None:
        raise ValueError(
            "Use view='data' for a numerical dataset; do not mix it with mission inputs"
        )
    if view == "orbit" and ui["presentation"] == "timeline":
        raise ValueError(
            "Observation/gap timelines require a coverage scenario; an orbit alone has no sensor coverage"
        )
    orbit_options = (satellite_identifiers, start_time, duration, display_step_seconds)
    if view == "example":
        if scenario is not None or any(value is not None for value in orbit_options):
            raise ValueError(
                "The example view takes only view='example'; custom inputs are not used as demo settings"
            )
        return _coverage_example(ui)
    if view == "coverage":
        if any(value is not None for value in orbit_options):
            raise ValueError(
                "Put coverage inputs and display sampling inside scenario; do not mix orbit arguments with a coverage study"
            )
        if analysis_id is not None:
            if scenario is not None:
                raise ValueError("Supply either scenario or analysis_id, not both")
            with _STUDIES_LOCK:
                scenario, _ = _STUDIES.get(analysis_id, (None, None))
            if scenario is None:
                raise ValueError(
                    f"No study with analysis_id {analysis_id!r} is available on the server. "
                    "Use the analysis_id stated in the earlier result, or resend that "
                    "study's full scenario; for the built-in example use view='example'. "
                    "Do not invent a new scenario."
                )
        if scenario is None:
            raise ValueError(
                "Coverage requires a supplied scenario. Ask the user which satellites and sensor assumptions to use; do not invent them"
            )
        return _coverage_explorer_result(scenario, ui=ui)
    if scenario is not None:
        raise ValueError(
            "The orbit view uses satellite_identifiers; use view='coverage' for a supplied coverage scenario"
        )
    if satellite_identifiers is None:
        raise ValueError(
            "Specify satellite_identifiers for the orbit view; resolve ambiguous names before calling"
        )
    if not 1 <= len(satellite_identifiers) <= 4:
        raise ValueError(
            "The orbit view supports 1–4 satellite_identifiers; ask the user which to show"
        )
    if display_step_seconds is not None and not 30 <= display_step_seconds <= 300:
        raise ValueError(
            "display_step_seconds must be an integer from 30 to 300; omit it for 60 seconds"
        )
    return _orbit_view(
        satellite_identifiers,
        start_time if start_time is not None else "now",
        duration if duration is not None else "1 hour",
        display_step_seconds if display_step_seconds is not None else 60,
        ui=ui,
    )


def _orbit_view(
    satellite_identifiers: Annotated[list[str], Field(min_length=1, max_length=4)],
    start_time: str = "now",
    duration: str = "1 hour",
    display_step_seconds: Annotated[int, Field(strict=True, ge=30, le=300)] = 60,
    ui: Optional[dict] = None,
) -> ToolResult:
    """Build a catalog orbit scene without inferring sensors or coverage."""
    from tatc.schemas import GeneralPerturbationsOrbit

    start = parse_time_input(start_time).replace(tzinfo=timezone.utc)
    end = start + parse_duration(duration)
    seconds = (end - start).total_seconds()
    if not 0 < seconds <= 86400:
        raise ValueError(
            f"Requested duration {duration!r} is outside the supported range: "
            "positive and at most 24 hours. Do not shorten or retry this request "
            "with changed inputs. Explain the limit and ask the user whether "
            "they want a shorter view."
        )
    if (math.ceil(seconds / display_step_seconds) + 1) * len(
        satellite_identifiers
    ) > 6000:
        raise ValueError(
            "Display exceeds 6000 samples. Explain the limit and ask the user "
            "whether to use coarser display sampling or a smaller study. "
            "Do not silently change the requested time window or satellites."
        )
    sources, tracks, ids = [], [], set()
    for identifier in satellite_identifiers:
        info = celestrak_client.get_satellite_info(identifier)
        norad_id = str(info["norad_id"])
        if norad_id in ids:
            raise ValueError(f"Duplicate satellite: NORAD {norad_id}")
        ids.add(norad_id)
        orbit = GeneralPerturbationsOrbit.from_tle(
            [info["tle_line1"], info["tle_line2"]]
        )
        sources.append({**info, "orbit_epoch": timestamp(orbit.get_epoch())})
        tracks.append(
            sample_orbit(
                f"{info['name']} ({norad_id})", orbit, start, end, display_step_seconds
            )
        )
    summary = {
        "start_time": timestamp(start),
        "end_time": timestamp(end),
        "display_step_seconds": display_step_seconds,
        "satellites": sources,
        "source": "CelesTrak TLEs propagated with TATC; interpolated display, no coverage analysis",
    }
    result = {
        "scenario": summary,
        "tracks": tracks,
        "points": [],
        "ui": ui or {},
        "request": {
            "view": "orbit",
            "satellite_identifiers": satellite_identifiers,
            "start_time": timestamp(start),
            "duration": duration,
            "display_step_seconds": display_step_seconds,
        },
    }
    return ToolResult(
        content=[
            TextContent(
                type="text",
                text=(
                    f"Orbit view for {len(tracks)} satellites, "
                    f"{timestamp(start)} to {timestamp(end)}, "
                    f"sampled every {display_step_seconds} seconds. Playback starts paused. "
                    "Orbital data: CelesTrak TLEs propagated with TATC, no sensors: "
                    + "; ".join(
                        f"{s['name']} (NORAD {s['norad_id']}), TLE epoch {s['orbit_epoch']}"
                        for s in sources
                    )
                    + ". Place the UI marker in your reply. For you only, not the user: "
                    "only if the user later asks for another view of these orbits, call visualize("
                    f"view='orbit', satellite_identifiers={json.dumps(satellite_identifiers)}, "
                    f"start_time='{timestamp(start)}', duration='{duration}', "
                    f"display_step_seconds={display_step_seconds}, presentation=<one>). "
                    "Do not describe this to the user or claim views that were not returned."
                ),
            ),
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(
                    uri=f"ui://tatc/orbits/{uuid4()}",
                    mimeType="text/uri-list",
                    text=build_viewer_url(result),
                ),
            ),
        ],
        structured_content=summary,
    )


@mcp.tool
def analyze_coverage(scenario: json_string_or(CoverageRequest)) -> Dict[str, Any]:
    """Analyze TATC observation opportunities and coverage gaps without opening a visual.

    Use for numerical or informational coverage questions. Only use the coverage
    view of the visualize tool instead when the request clearly implies visualization.

    If the user has not supplied or agreed to satellites and sensor assumptions,
    ask for them before calling. Never invent TLEs or instrument field of regard.
    Not supported, so explain instead of calling: swath rendering, footprints of
    basic field-of-regard instruments, solar-illumination masks, observation
    scheduling/tasking, ground-station downlink/latency, area/regional heatmaps,
    cost models or design-space optimization. Never invent TLEs, orbits or field of regard to approximate them.

    Accepts a scenario with satellites (native TATC Satellite JSON), named points,
    explicit start_time/end_time with UTC offsets, and optional display_step_seconds.
    Supports hypothetical orbit designs and supplied tle orbits (type='tle',
    tle=[line1, line2]).
    Each satellite must specify instruments with name and field_of_regard in degrees.
    A pointed instrument adds cross_track_field_of_view and along_track_field_of_view
    (degrees) with optional roll_angle, pitch_angle and is_rectangular; any instrument
    may set min_access_time (seconds), req_self_sunlit and req_target_sunlit.
    For a circular orbit supply type='circular', mean_altitude in meters, inclination
    in degrees, epoch with a UTC offset, and optional right_ascension_ascending_node
    (RAAN) and true_anomaly in degrees; the names are exact. Points use name, latitude, longitude,
    and optional WGS84 elevation in meters.

    Limits: 4 satellites, 2 instruments each, 8 points, 24 hours, and 384
    point-instrument-hours. Returns inputs, assumptions, individual observations,
    merged coverage, gaps (including boundaries), and metrics. No area coverage is
    inferred between points. Mean complete revisit excludes window boundaries and
    is null when no complete gap exists. For visualization use the visualize tool with view=coverage
    and the returned analysis_id. This computes opportunities, not executed tasks.
    """
    result = compute_coverage_analysis(scenario)
    _remember_study(result["analysis_id"], scenario, result["points"])
    return result


# Recent scenarios by analysis_id, so a follow-up can re-show a study without the
# model resending (or reinventing) its inputs. Kept in memory only.
# Point metrics are kept too, to check charted study values.
_STUDIES: Dict[str, Tuple[CoverageRequest, Dict[str, Dict[str, Any]]]] = {}
_STUDIES_LOCK = Lock()


def _remember_study(analysis_id: str, scenario: CoverageRequest, points) -> None:
    with _STUDIES_LOCK:
        _STUDIES.pop(analysis_id, None)
        _STUDIES[analysis_id] = (scenario, {p["name"]: p["metrics"] for p in points})
        if len(_STUDIES) > 100:
            del _STUDIES[next(iter(_STUDIES))]


def _check_study_values(dataset: ResearchData) -> None:
    """Reject charted 'study' values that are not that study's point metrics."""
    analysis_id = dataset.provenance.analysis_id
    with _STUDIES_LOCK:
        _, metrics = _STUDIES.get(analysis_id, (None, None))
    if metrics is None:
        raise ValueError(
            f"No study with analysis_id {analysis_id!r} is available on the server. "
            "Use the analysis_id stated in the study result; do not chart values from memory."
        )
    if any(label not in metrics for label in dataset.labels):
        raise ValueError(
            f"Labels must be study point names of {analysis_id}: {', '.join(metrics)}"
        )
    # The values stated in the study result text, by metric.
    stated = {
        name: (
            m["observation_count"],
            m["coverage_fraction"] * 100,
            m["longest_gap_seconds"] / 60,
            (
                None
                if m["mean_complete_revisit_seconds"] is None
                else m["mean_complete_revisit_seconds"] / 60
            ),
        )
        for name, m in metrics.items()
    }

    def same(value, expected):
        if value is None or expected is None:
            return value is expected
        # Allow the rounding of a value copied from the result text.
        return math.isclose(value, expected, rel_tol=1e-3, abs_tol=0.01)

    for series in dataset.series:
        if not any(
            all(
                same(value, stated[label][metric])
                for label, value in zip(dataset.labels, series.values)
            )
            for metric in range(4)
        ):
            raise ValueError(
                f"Series {series.name!r} does not match study {analysis_id}. Each series "
                "must copy one metric from the study result for each labelled point: "
                "observations, percent covered, longest gap minutes, or mean complete "
                "revisit minutes (null when none). Do not chart invented or converted values."
            )


def _study_text(result: dict, scenario: CoverageRequest, example: bool) -> str:
    """Exact compact results: LibreChat keeps only text content for the model."""
    lines = [
        (
            "Built-in hypothetical mission example (fixed dates; not live satellite data). "
            if example
            else ""
        )
        + f"Coverage study analysis_id={result['analysis_id']}, "
        f"{timestamp(scenario.start_time)} to {timestamp(scenario.end_time)} UTC, "
        f"display step {scenario.display_step_seconds} s.",
        # The model otherwise relabels this result as whatever was asked for.
        "Scope: access opportunities at the listed study points only. Nothing here is an "
        "area heatmap, swath, illumination mask, observation schedule, downlink or latency, "
        "or cost result; never describe it as one, and if the user asked for one, say that "
        "it is not supported. The study map draws the instantaneous TATC footprint and "
        "boresight of pointed instruments only (those with cross_track_field_of_view), "
        "throughout the window, whether or not a study point is observed.",
        f"Satellites ({len(scenario.satellites)}; name each in your reply):",
    ]
    for satellite in scenario.satellites:
        # Every supplied setting, since pointing and constraints change the results.
        instruments = ", ".join(
            f"{i['name']} {json.dumps({k: v for k, v in i.items() if k != 'name'})}"
            for i in satellite["instruments"]
        )
        lines.append(
            f"- {satellite['name']}: orbit {json.dumps(satellite['orbit'])}; instruments {instruments}"
        )
    lines.append(
        "Study points (TATC event-based results; longest gap includes window-boundary "
        "gaps; mean complete revisit excludes them):"
    )
    for point in result["points"]:
        metrics = point["metrics"]
        longest = max(point["gaps"], key=lambda g: g["duration_seconds"], default=None)
        revisit = metrics["mean_complete_revisit_seconds"]
        lines.append(
            f"- {point['name']} (lat {point['latitude']}, lon {point['longitude']}, "
            f"elevation {point['elevation']} m): {metrics['observation_count']} observations, "
            f"{metrics['coverage_fraction'] * 100:.2f}% covered, longest gap "
            f"{metrics['longest_gap_seconds'] / 60:.3f} min"
            + (" (at window boundary)" if longest and longest["boundary"] else "")
            + ", mean complete revisit "
            + (
                "none (no complete revisit)"
                if revisit is None
                else f"{revisit / 60:.3f} min"
            )
        )
    reshow = (
        "view='example'"
        if example
        else f"view='coverage', analysis_id='{result['analysis_id']}'"
    )
    lines.append(
        "Answer follow-up questions about this study from these values; do not recalculate it "
        "or build another scenario. Place the UI marker in your reply. For you only, not the "
        "user: only if the user later asks for another view of this study, call visualize("
        f"{reshow}, presentation=<one>); to chart these values use dataset provenance "
        f"{{'kind': 'study', 'analysis_id': '{result['analysis_id']}'}}. Do not describe "
        "this to the user or claim views that were not returned."
    )
    return "\n".join(lines)


def _coverage_example(ui=None) -> ToolResult:
    """Load the explicitly requested, fixed hypothetical study."""
    fixture = Path(__file__).with_name("coverage-scenario.json")
    scenario = CoverageRequest.model_validate_json(fixture.read_text(encoding="utf-8"))
    return _coverage_explorer_result(scenario, example=True, ui=ui)


def _coverage_explorer_result(
    scenario: CoverageRequest, example: bool = False, ui=None
) -> ToolResult:
    result = compute_coverage_analysis(scenario, include_tracks=True)
    _remember_study(result["analysis_id"], scenario, result["points"])
    result["ui"] = ui or {}
    result["request"] = {
        "view": "coverage",
        "scenario": scenario.model_dump(mode="json"),
    }
    if example:
        result["assumptions"].insert(
            0,
            "Built-in example: hypothetical mission with fixed October 7–8, 2026 "
            "dates; not live satellite data.",
        )
    summary = {
        "analysis_id": result["analysis_id"],
        "example": example,
        "points": [{"name": p["name"], **p["metrics"]} for p in result["points"]],
        "assumptions": result["assumptions"],
    }
    return ToolResult(
        content=[
            TextContent(type="text", text=_study_text(result, scenario, example)),
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(
                    uri=f"ui://tatc/coverage/{uuid4()}",
                    mimeType="text/uri-list",
                    text=build_viewer_url(result),
                ),
            ),
        ],
        structured_content=summary,
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
def search_satellites(query: str, limit: int = 10) -> List[Dict[str, Any]] | str:
    """Search currently orbiting satellites by name in the CelesTrak database.

    Decayed objects are excluded so returned names and NORAD IDs work with
    the other tools. Use this first when the exact name is unknown.

    Args:
        query: Satellite name or partial name (e.g., "Starlink", "GPS").
        limit: Maximum number of results, between 1 and 50. Defaults to 10.

    Returns:
        Objects with norad_id, name, object_type, country, and launch_date, or
        a message when nothing matches; upstream failures are reported as errors.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer between 1 and 50")
    results = celestrak_client.search_satellites_by_name(query, limit=limit)
    # LibreChat shows an empty list to the model as "(No response)".
    if not results:
        return (
            f"No orbiting satellite matches {query!r}. It may be a place or a "
            "non-catalog name; ask the user which satellite they mean. Do not "
            "invent a satellite or its TLE."
        )
    return results


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
