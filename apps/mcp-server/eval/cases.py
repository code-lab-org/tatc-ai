"""Coverage conversations the eval asks every model, with their checks.

Each case is one researcher request. Checks read the trace of tool calls
and the final reply, and look at what the server actually computed (the
saved summary), not at what the model claims. Prompts use dates relative
to the eval's pinned "now" so a replayed cache keeps working.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

FINISH_TOOLS = {
    "finish_access_run",
    "finish_short_access_run",
    "finish_year_access_run",
    "run_satellite_access",
}

PHOENIX = (33.45, -112.07)
TEMPE = (33.42, -111.94)
DENVER = (39.74, -104.99)
GRAND_JUNCTION = (39.06, -108.55)


@dataclass
class Call:
    name: str
    args: Dict[str, Any]
    error: bool
    text: str
    parsed: Optional[Dict[str, Any]] = None


@dataclass
class Trace:
    prompt: str
    calls: List[Call] = field(default_factory=list)
    final: str = ""
    turns: int = 0
    stop: str = ""


@dataclass
class Case:
    id: str
    tags: Tuple[str, ...]
    prompt: str
    checks: Callable[[Trace, "Clock"], Dict[str, bool]]


@dataclass
class Clock:
    now: datetime

    @property
    def d0(self) -> datetime:
        """Next UTC midnight after now."""
        return (self.now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    def fmt(self, value: datetime) -> str:
        return value.strftime("%Y-%m-%d %H:%M UTC")


def _numbers(text: str) -> List[float]:
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", text)]


def _sourced(value: Any, texts: List[str]) -> bool:
    """Whether a geometry number appeared in the prompt or a tool result."""
    try:
        target = float(value)
    except (TypeError, ValueError):
        return False
    tolerance = max(1e-6, 0.005 * abs(target))
    return any(abs(n - target) <= tolerance for t in texts for n in _numbers(t))


def successful_finish(trace: Trace) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Last finish that returned a result, plus texts the model had seen."""
    seen = [trace.prompt]
    found: Tuple[Optional[Dict[str, Any]], List[str]] = (None, [])
    for call in trace.calls:
        if call.name in FINISH_TOOLS and not call.error and call.parsed and call.parsed.get("result_id"):
            found = (call.parsed, list(seen))
        seen.append(call.text)
    return found


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _window(summary: Dict[str, Any]) -> Tuple[datetime, timedelta]:
    start = _parse(summary["window_start_utc"])
    return start, _parse(summary["window_end_utc_exclusive"]) - start


def _contains(summary: Dict[str, Any], points, max_span: float, slack: float = 0.2) -> bool:
    """Box reaches each point within slack degrees and is not oversized."""
    west, south, east, north = summary["roi_bbox"]
    inside = all(
        west - slack <= lon <= east + slack and south - slack <= lat <= north + slack
        for lat, lon in points
    )
    return inside and east - west <= max_span and north - south <= max_span


def _geometry(summary: Dict[str, Any], kind: str, value: float) -> bool:
    geometry = summary["geometry_model"]
    if geometry["geometry_input_kind"] == kind:
        return abs(float(geometry["geometry_input_value"]) - value) <= 0.005 * value
    if kind == "swath_width_km":
        # A field of regard the converter produced from the right swath is fine.
        return abs(geometry["modeled_swath_width_km_at_omm_mean_altitude"] - value) <= 0.01 * value
    return False


def _base_checks(summary, seen, points, max_span, window_ok, kind, value):
    if summary is None:
        return {"completed": False, "window": False, "region": False, "geometry": False, "sourced": False}
    start, duration = _window(summary)
    geometry = summary["geometry_model"]
    return {
        "completed": True,
        "window": window_ok(start, duration),
        "region": _contains(summary, points, max_span),
        "geometry": _geometry(summary, kind, value),
        "sourced": _sourced(geometry["geometry_input_value"], seen),
    }


def _given(points, max_span, kind, value, window):
    def checks(trace: Trace, clock: Clock) -> Dict[str, bool]:
        summary, seen = successful_finish(trace)
        return _base_checks(
            summary, seen, points, max_span,
            lambda start, duration: window(clock, start, duration), kind, value,
        )

    return checks


def _exact(days: float):
    return lambda clock, start, duration: start == clock.d0 and duration == timedelta(days=days)


def _around_now(low_hours: float, high_hours: float):
    """Starts between today's UTC midnight and tomorrow's end, sized right."""

    def ok(clock: Clock, start: datetime, duration: timedelta) -> bool:
        near = clock.d0 - timedelta(days=1) <= start <= clock.d0 + timedelta(days=1)
        return near and timedelta(hours=low_hours) <= duration <= timedelta(hours=high_hours)

    return ok


def _asks_instead(trace: Trace, clock: Clock) -> Dict[str, bool]:
    """The resolver says needs_review: no run, and the reply raises geometry."""
    summary, _ = successful_finish(trace)
    text = trace.final.lower()
    words = ("swath", "field of regard", "angle", "geometry", "conical")
    # Text that only imitates tool calls is not an answer to the researcher.
    raised = any(word in text for word in words) and "_mcp_tatc(" not in text
    return {"did_not_run": summary is None, "raised_geometry": raised}


def _gap_cells(trace: Trace, clock: Clock) -> Dict[str, bool]:
    """A narrow swath leaves cells unseen; the reply must report them."""
    checks = _given([DENVER, GRAND_JUNCTION], 10.0, "swath_width_km", 185.0, _exact(1))(trace, clock)
    summary, _ = successful_finish(trace)
    never = summary["summary_counts"]["never_observed_cell_count"] if summary else None
    checks["fetched_cells"] = any(
        call.name == "get_access_cells" and not call.error for call in trace.calls
    )
    # The number of unseen cells, as digits, must appear in the reply.
    checks["reported_gaps"] = never is not None and str(never) in re.findall(r"\d+", trace.final)
    return checks


def build_cases(clock: Clock) -> List[Case]:
    d0 = clock.fmt(clock.d0)
    modis = "Use a field of regard of 110 degrees, from the NASA MODIS design page."
    return [
        Case(
            "short_given", ("short", "geometry_given"),
            f"Estimate Terra access over Phoenix, Arizona for 24 hours starting {d0}. {modis}",
            _given([PHOENIX], 5.0, "field_of_regard_deg", 110.0, _exact(1)),
        ),
        Case(
            "long_given", ("long", "geometry_given"),
            f"Estimate Terra access over Phoenix, Arizona for 60 days starting {d0}, with daily results. {modis}",
            _given([PHOENIX], 5.0, "field_of_regard_deg", 110.0, _exact(60)),
        ),
        Case(
            "boundary_given", ("long", "geometry_given", "boundary"),
            f"Estimate Terra access over Phoenix, Arizona from {d0} to "
            f"{clock.fmt(clock.d0 + timedelta(days=4, hours=12))}. {modis}",
            _given([PHOENIX], 5.0, "field_of_regard_deg", 110.0, _exact(4.5)),
        ),
        Case(
            "short_resolve", ("short", "geometry_resolved"),
            "How often can the imager on Sentinel-2A see Tempe, Arizona over the next 2 days?",
            _given([TEMPE], 5.0, "swath_width_km", 290.0, _around_now(36, 60)),
        ),
        Case(
            "long_resolve", ("long", "geometry_resolved"),
            "How much of Colorado can Landsat 9's OLI imager cover over the next 30 days, and how long are the gaps?",
            _given([DENVER, GRAND_JUNCTION], 10.0, "swath_width_km", 185.0, _around_now(28 * 24, 32 * 24)),
        ),
        Case(
            "needs_review", ("geometry_unclear",),
            "How often can the radar on Sentinel-1A see Phoenix, Arizona over the next 2 days?",
            _asks_instead,
        ),
        Case(
            "fetch_gaps", ("short", "geometry_given", "fetch"),
            f"Estimate Landsat 9 access over Colorado for 24 hours starting {d0}, using its "
            "185 km swath from the USGS Landsat 9 page. Which parts of the state were never observed?",
            _gap_cells,
        ),
    ]
