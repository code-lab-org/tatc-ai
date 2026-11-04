"""Bounded TATC study-point analysis, independent of the visualization."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from importlib.metadata import version
from itertools import pairwise
import json
import math
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from .conical_accounting import ACCOUNTING_METHOD, POINTED_ACCOUNTING_METHOD, _stats
from .tatc_integration import create_orbit_from_omm

# SGP4 error grows with time from the TLE epoch (for LEO roughly 1–3 km/day,
# dominated by drag), so pass timing from an element set weeks away from the
# window is not meaningful. 30 days still allows planning from a current TLE;
# it also rejects element sets a model wrote from memory with a stale epoch.
MAX_TLE_EPOCH_DAYS = 30
# Footprints of 4 satellites with 2 pointed instruments each over 24 hours at
# the default 120 s display step; each sample adds a polygon to the scene.
MAX_FOOTPRINT_SAMPLES = 5768
INTERVAL_ACCOUNTING_METHOD = "interval_union"
TLE_SOURCE_HINT = (
    "Use the OMM records returned by the satellite info tool as an omm orbit for current "
    "windows, or TLE lines the user supplied for this window. Never write orbital elements "
    "yourself; ask the user."
)


# LibreChat validates tool arguments against the JSON schema and reports any
# failure to the model only as "did not match expected schema". Required keys,
# ranges, list sizes and formats are therefore checked by validators, whose
# messages reach the model and say how to correct the call.
def _no_required(schema: dict) -> None:
    schema.pop("required", None)


def _json_object(value):
    """Accept an object sent as a JSON-encoded string, as weak tool callers do."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Expected a JSON object, but the string is not valid JSON ({exc}). "
                "Send the object itself."
            ) from None
    return value


class _AlsoJsonString:
    def __get_pydantic_json_schema__(self, core_schema, handler):
        return {"anyOf": [handler(core_schema), {"type": "string"}]}


def json_string_or(model):
    """An object argument that may also arrive as its JSON string."""
    return Annotated[model, BeforeValidator(_json_object), _AlsoJsonString()]


class StudyPoint(BaseModel):
    """A named WGS84 location; no area coverage is inferred between points."""

    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
        str_strip_whitespace=True,
        json_schema_extra=_no_required,
    )
    name: str = Field(min_length=1, max_length=80)
    latitude: float = Field(description="Degrees, -90 to 90.")
    longitude: float = Field(description="Degrees, -180 to 180.")
    elevation: float = Field(default=0, description="WGS84 meters, -500 to 10000.")

    @model_validator(mode="after")
    def in_range(self):
        for field, low, high in (
            ("latitude", -90, 90),
            ("longitude", -180, 180),
            ("elevation", -500, 10000),
        ):
            if not low <= getattr(self, field) <= high:
                raise ValueError(f"{self.name}: {field} must be from {low} to {high}")
        return self


# Without a format, a relative date reaches the validator instead of the schema.
UtcTime = Annotated[datetime, WithJsonSchema({"type": "string"})]


class CoverageRequest(BaseModel):
    """Explicit scenario inputs shared by numerical and graphical tools."""

    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, json_schema_extra=_no_required
    )
    satellites: list[dict[str, Any]] = Field(
        description="1–4 TATC Satellite JSON objects: name, orbit, and explicit instruments. "
        "Each instrument needs name and field_of_regard (degrees). A pointed instrument "
        "also has cross_track_field_of_view and along_track_field_of_view (degrees), and "
        "optional roll_angle, pitch_angle (degrees) and is_rectangular; any instrument may "
        "set min_access_time (seconds), req_self_sunlit and req_target_sunlit (true for "
        "sunlit, false for eclipse). Supply an orbit "
        "type and epoch, an omm orbit with elements=[records] copied from the satellite "
        "info tool, or a tle orbit with both TLE lines from the user; element sets need "
        "an epoch within 30 days of the window. "
        "Hypothetical circular orbits use mean_altitude (meters), inclination (degrees), "
        "and optional right_ascension_ascending_node and true_anomaly (degrees).",
    )
    points: list[StudyPoint] = Field(description="1–8 named study points.")
    start_time: UtcTime = Field(description="ISO-8601 time with Z or a UTC offset.")
    end_time: UtcTime = Field(
        description="ISO-8601 time; at most 24 hours after start."
    )
    display_step_seconds: int = Field(
        default=120,
        strict=True,
        description="30–300 seconds. Orbit display sampling only; does not determine observation intervals.",
    )

    @model_validator(mode="before")
    @classmethod
    def top_level_inputs(cls, data):
        if isinstance(data, dict):
            if not data.get("satellites"):
                raise ValueError(
                    "No satellites or sensors were supplied; ask the user which "
                    "satellites and sensor field of regard to study; do not invent them."
                )
            missing = [k for k in ("points", "start_time", "end_time") if k not in data]
            if missing:
                raise ValueError(
                    f"The scenario is missing {', '.join(missing)}. Its top-level keys "
                    "are satellites, points, start_time, end_time and optional "
                    "display_step_seconds; do not nest points or times inside satellites."
                )
        return data

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def iso_time(cls, value, info):
        if isinstance(value, str):
            try:
                datetime.fromisoformat(value)
            except ValueError:
                raise ValueError(
                    f"{info.field_name} {value!r} is not a timestamp. start_time/end_time "
                    "must be explicit ISO 8601 UTC timestamps; resolve relative dates "
                    "such as 'tomorrow' with the user before calling."
                ) from None
        return value

    @field_validator("start_time", "end_time")
    @classmethod
    def explicit_timezone(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Times must include Z or a UTC offset")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def bounded_window(self):
        if not 1 <= len(self.satellites) <= 4 or not 1 <= len(self.points) <= 8:
            raise ValueError(
                "A study supports 1–4 satellites and 1–8 study points. Explain the "
                "limit and ask the user which to use; do not invent or drop inputs"
            )
        if not 30 <= self.display_step_seconds <= 300:
            raise ValueError("display_step_seconds must be from 30 to 300")
        seconds = (self.end_time - self.start_time).total_seconds()
        if not 0 < seconds <= 86400:
            raise ValueError(
                "Analysis window must be positive and at most 24 hours. Explain the "
                "limit and ask the user before shortening it"
            )
        if len({p.name for p in self.points}) != len(self.points):
            raise ValueError("Study point names must be unique")
        samples = math.ceil(seconds / self.display_step_seconds) + 1
        if samples * len(self.satellites) > 6000:
            raise ValueError(
                "Display exceeds 6000 samples; increase display_step_seconds"
            )
        # Reject non-finite numbers and unreasonable payloads before invoking TATC.
        try:
            payload = json.dumps(self.satellites, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "Satellite definitions must be finite JSON values"
            ) from exc
        if len(payload) > 64000:
            raise ValueError("Satellite definitions exceed 64 KB")
        return self


def timestamp(value: datetime) -> str:
    # TATC 3.6 returns pandas timestamps with nanoseconds; keep microseconds.
    if hasattr(value, "to_pydatetime"):
        value = value.floor("us").to_pydatetime()
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _reject_unknown(raw, model):
    """TATC ignores extra fields; reject typos rather than silently alter a study."""
    fields = type(model).model_fields
    allowed = {name: name for name in fields}
    for name, field in fields.items():
        alias = field.validation_alias
        if isinstance(alias, str):
            allowed[alias] = name
        elif hasattr(alias, "choices"):
            allowed.update({x: name for x in alias.choices if isinstance(x, str)})
    unknown = set(raw) - allowed.keys()
    if unknown:
        # Name the accepted fields so a model can correct the call itself.
        raise ValueError(
            f"Unknown {type(model).__name__} fields: {', '.join(sorted(unknown))}. "
            f"Accepted fields: {', '.join(sorted(allowed))}"
        )
    for key, value in raw.items():
        parsed = getattr(model, allowed[key])
        if isinstance(value, dict) and isinstance(parsed, BaseModel):
            _reject_unknown(value, parsed)
        elif isinstance(value, list) and isinstance(parsed, (list, tuple)):
            for item, parsed_item in zip(value, parsed):
                if isinstance(item, dict) and isinstance(parsed_item, BaseModel):
                    _reject_unknown(item, parsed_item)


def _tle_checksum(line: str) -> int:
    """Compute the TLE checksum of the first 68 characters of a line."""
    total = 0
    for char in line[:68]:
        if char.isdigit():
            total += int(char)
        elif char == "-":
            total += 1
    return total % 10


def validate_tle_format(tle_line1: str, tle_line2: str) -> tuple[str, str]:
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


def check_tle(tle, start: datetime, end: datetime):
    """Reject malformed or out-of-date element sets before TATC propagates them."""
    if not isinstance(tle, list) or len(tle) != 2:
        raise ValueError("A tle orbit needs tle=[line1, line2]. " + TLE_SOURCE_HINT)
    try:
        line1, line2 = validate_tle_format(*tle)
    except ValueError as exc:
        raise ValueError(f"Invalid TLE: {exc}. {TLE_SOURCE_HINT}") from None
    if len(line1) != 69 or len(line2) != 69 or line1[2:7] != line2[2:7]:
        raise ValueError(
            "Invalid TLE: lines must be 69 characters with the same catalog "
            f"number. {TLE_SOURCE_HINT}"
        )
    year = int(line1[18:20])
    epoch = datetime(
        year + (1900 if year >= 57 else 2000), 1, 1, tzinfo=timezone.utc
    ) + timedelta(days=float(line1[20:32]) - 1)
    check_element_epoch(epoch, f"catalog {line1[2:7]}", start, end)


def check_element_epoch(epoch: datetime, label: str, start: datetime, end: datetime):
    """Reject element sets whose epoch is too far from the window for SGP4."""
    days = (
        max((start - epoch).total_seconds(), (epoch - end).total_seconds(), 0) / 86400
    )
    if days > MAX_TLE_EPOCH_DAYS:
        raise ValueError(
            f"Element epoch {epoch:%Y-%m-%d} ({label}) is {days:.0f} days "
            f"from the study window; SGP4 is not reliable beyond {MAX_TLE_EPOCH_DAYS} "
            f"days. {TLE_SOURCE_HINT}"
        )


def build_satellites(request: CoverageRequest):
    """Validate explicit assumptions, retaining TATC's native schema flexibility."""
    from tatc.schemas import Satellite

    satellites = []
    for raw in request.satellites:
        instruments = raw.get("instruments")
        if not isinstance(instruments, list) or not 1 <= len(instruments) <= 2:
            raise ValueError("Each satellite requires 1–2 explicit instruments")
        for instrument in instruments:
            if (
                not isinstance(instrument, dict)
                or not {"name", "field_of_regard"} <= instrument.keys()
            ):
                raise ValueError("Each instrument requires name and field_of_regard")
        orbit = raw.get("orbit")
        if not isinstance(orbit, dict) or "type" not in orbit:
            raise ValueError("Each satellite requires an orbit with an explicit type")
        if orbit["type"] == "tle":
            check_tle(orbit.get("tle"), request.start_time, request.end_time)
        if orbit["type"] == "omm":
            # TATC stores CelesTrak OMM records as a gp orbit in its own units.
            orbit = create_orbit_from_omm(orbit.get("elements")).model_dump(mode="json")
            raw = {**raw, "orbit": orbit}
        satellite = Satellite.model_validate(raw)
        _reject_unknown(raw, satellite)
        for instrument in satellite.instruments:
            if not instrument.name.strip() or len(instrument.name) > 80:
                raise ValueError("Instrument names must contain 1–80 characters")
            if instrument.min_access_time < timedelta(0):
                raise ValueError("Minimum access time must not be negative")
            if (
                instrument.access_time_fixed
                and instrument.min_access_time <= timedelta(0)
            ):
                raise ValueError("Fixed access time must be positive")
        if len({i.name for i in satellite.instruments}) != len(satellite.instruments):
            raise ValueError("Instrument names must be unique within a satellite")
        if not satellite.name.strip() or len(satellite.name) > 80:
            raise ValueError("Satellite names must contain 1–80 characters")
        epoch = satellite.orbit.get_epoch()
        if epoch.tzinfo is None or epoch.utcoffset() is None:
            raise ValueError("Orbit epochs must include Z or a UTC offset")
        # Keplerian-family defaults use a historical epoch: require user intent.
        if (
            orbit["type"] != "tle"
            and hasattr(satellite.orbit, "epoch")
            and "epoch" not in orbit
        ):
            raise ValueError("Supply an explicit orbit epoch")
        if orbit["type"] == "gp":
            check_element_epoch(
                epoch, satellite.name, request.start_time, request.end_time
            )
        # Pydantic may coerce strings such as 'NaN'; validate the parsed output too.
        json.dumps(satellite.model_dump(mode="python"), default=str, allow_nan=False)
        satellites.append(satellite)
    if len({s.name for s in satellites}) != len(satellites):
        raise ValueError("Satellite names must be unique")
    hours = (request.end_time - request.start_time).total_seconds() / 3600
    workload = len(request.points) * sum(len(s.instruments) for s in satellites) * hours
    if workload > 384:
        raise ValueError(
            "Study exceeds 384 point-instrument-hours; reduce locations or duration"
        )
    return satellites


def summarize_intervals(observations, start: datetime, end: datetime):
    """Union coverage and its complement in [start, end]; preserve raw contributors."""
    intervals = []
    for observation in observations:
        left = max(start, datetime.fromisoformat(observation["start"]))
        right = min(end, datetime.fromisoformat(observation["end"]))
        if right <= left:
            continue
        intervals.append(
            (left, right, observation["satellite"], observation["instrument"])
        )
    intervals.sort()
    merged = []
    for left, right, satellite, instrument in intervals:
        contributor = (satellite, instrument)
        if merged and left <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], right)
            merged[-1][2].add(contributor)
        else:
            merged.append([left, right, {contributor}])
    covered = [
        {
            "start": timestamp(left),
            "end": timestamp(right),
            "duration_seconds": (right - left).total_seconds(),
            "contributors": [
                {"satellite": s, "instrument": i} for s, i in sorted(contributors)
            ],
        }
        for left, right, contributors in merged
    ]
    gaps = []
    cursor = start
    for left, right, _ in merged:
        if left > cursor:
            gaps.append((cursor, left))
        cursor = right
    if cursor < end:
        gaps.append((cursor, end))
    gap_records = [
        {
            "start": timestamp(left),
            "end": timestamp(right),
            "duration_seconds": (right - left).total_seconds(),
            "boundary": left == start or right == end,
        }
        for left, right in gaps
    ]
    complete = [g["duration_seconds"] for g in gap_records if not g["boundary"]]
    covered_seconds = sum(i["duration_seconds"] for i in covered)
    return {
        "coverage_intervals": covered,
        "gaps": gap_records,
        "metrics": {
            "observation_count": len(intervals),
            "coverage_period_count": len(covered),
            "covered_seconds": covered_seconds,
            "coverage_fraction": covered_seconds / (end - start).total_seconds(),
            "longest_gap_seconds": max(
                (g["duration_seconds"] for g in gap_records), default=0
            ),
            "mean_complete_revisit_seconds": (
                sum(complete) / len(complete) if complete else None
            ),
            "continuous_viewing_duration_available": True,
            "continuous_uncovered_duration_available": True,
            "accounting_method": INTERVAL_ACCOUNTING_METHOD,
        },
    }


def summarize_events(observations, accounting_method: str):
    """Per-point metrics at valid viewing epochs of pointed and conical instruments.

    TATC returns each epoch but no per-point summary, so this applies the
    definitions of conical_accounting to one point. Continuous metrics are null,
    not zero: the field-of-regard envelope is not viewing time, and the time
    between epochs is not an uncovered interval.
    """
    epochs = sorted(
        {datetime.fromisoformat(o["epoch"]) for o in observations if "envelope_start" in o}
    )
    spacings = [(right - left).total_seconds() / 3600 for left, right in pairwise(epochs)]
    return {
        "coverage_intervals": [],
        "gaps": [],
        "metrics": {
            "observation_count": len(observations),
            "distinct_event_count": len(epochs),
            "first_event_utc": timestamp(epochs[0]) if epochs else None,
            "last_event_utc": timestamp(epochs[-1]) if epochs else None,
            "event_spacing_sample_count": len(spacings),
            **_stats(spacings),
            "coverage_period_count": None,
            "covered_seconds": None,
            "coverage_fraction": None,
            "longest_gap_seconds": None,
            "mean_complete_revisit_seconds": None,
            "continuous_viewing_duration_available": False,
            "continuous_uncovered_duration_available": False,
            "accounting_method": accounting_method,
        },
    }


def _event_method(instrument) -> str | None:
    """The event accounting of an instrument whose TATC period is only an envelope."""
    from tatc.schemas import ConicalInstrument, PointedInstrument

    if isinstance(instrument, PointedInstrument):
        return POINTED_ACCOUNTING_METHOD
    if isinstance(instrument, ConicalInstrument):
        return ACCOUNTING_METHOD
    return None


def analyze(request: CoverageRequest, include_tracks=False) -> dict:
    """Compute event-based observations; orbit samples are solely a display artifact."""
    from tatc.analysis import collect_observations
    from tatc.schemas import Point, PointedInstrument

    satellites = build_satellites(request)
    scenario = request.model_dump(mode="json")
    # A PointedInstrument is also an Instrument, the first member of TATC's union;
    # without serialize_as_any its pointing is dropped from the scenario and the id.
    scenario["satellites"] = [
        s.model_dump(mode="json", serialize_as_any=True) for s in satellites
    ]
    scenario["orbit_epochs"] = {
        s.name: timestamp(s.orbit.get_epoch()) for s in satellites
    }
    software = {name: version(name) for name in ("tatc", "skyfield")}
    identity = json.dumps(
        {"scenario": scenario, "software": software}, sort_keys=True, allow_nan=False
    )
    event_methods = {
        method
        for satellite in satellites
        for instrument in satellite.instruments
        if (method := _event_method(instrument))
    }
    event_based = bool(event_methods)
    # One discrete event cannot be unioned with intervals, so any pointed or conical
    # instrument makes the study's continuous metrics unavailable.
    accounting_method = "+".join(sorted(event_methods)) or INTERVAL_ACCOUNTING_METHOD
    result = {
        "schema_version": "1.1",        "analysis_id": sha256(identity.encode()).hexdigest()[:20],
        "scenario": scenario,
        "software": software,
        "method": (
            "TATC collect_observations; valid viewing epochs per study point"
            if event_based
            else "TATC collect_observations; union of observation intervals per study point"
        ),
        "accounting": {
            "method": accounting_method,
            "event_based": event_based,
            "continuous_viewing_duration_available": not event_based,
            "continuous_uncovered_duration_available": not event_based,
        },
        "assumptions": [
            "Only the supplied study points are evaluated; no area coverage is inferred between them.",
            "Observation opportunities use the supplied instrument model, not an executed task schedule.",
            *(
                [
                    "Pointed and conical instruments count only TATC's valid viewing epochs. Their field-of-regard period is kept as envelope metadata; it is wider than the instrument's view and is not viewing time.",
                    "Time covered, covered duration, gaps and revisit are unavailable for this study, not zero. Event spacing is the time between distinct viewing epochs, not an uncovered interval.",
                    "Basic field-of-regard instruments in a study with pointed or conical instruments keep their own intervals, which do not count toward event spacing.",
                ]
                if event_based
                else [
                    "TATC checks some instrument and illumination constraints at pass midpoints; interval boundaries inherit that approximation.",
                    "Longest gap includes uncovered time at window boundaries; mean complete revisit excludes boundary gaps.",
                ]
            ),
            "Orbit display samples and interpolation do not determine coverage results. Heights use WGS84 meters.",
        ],
        "points": [],
    }
    event_count = 0
    for index, study_point in enumerate(request.points):
        point = Point(id=index, **study_point.model_dump(exclude={"name"}))
        observations = []
        for satellite in satellites:
            for instrument_index, instrument in enumerate(satellite.instruments):
                event_method = _event_method(instrument)
                frame = collect_observations(
                    point,
                    satellite,
                    request.start_time,
                    request.end_time,
                    instrument_index=instrument_index,
                    omit_solar=True,
                )
                for row in frame.itertuples():
                    if event_method:
                        # Half-open window, as in conical_accounting.
                        if request.start_time <= row.epoch < request.end_time:
                            observations.append(
                                {
                                    "satellite": satellite.name,
                                    "instrument": instrument.name,
                                    "epoch": timestamp(row.epoch),
                                    "envelope_start": timestamp(row.start),
                                    "envelope_end": timestamp(row.end),
                                    "envelope_duration_seconds": (
                                        row.end - row.start
                                    ).total_seconds(),
                                    "accounting_method": event_method,
                                }
                            )
                        continue
                    left, right = max(request.start_time, row.start), min(
                        request.end_time, row.end
                    )
                    if right <= left:
                        continue
                    observations.append(
                        {
                            "satellite": satellite.name,
                            "instrument": instrument.name,
                            "start": timestamp(left),
                            "end": timestamp(right),
                            "epoch": timestamp(row.epoch),
                            "duration_seconds": (right - left).total_seconds(),
                            "accounting_method": INTERVAL_ACCOUNTING_METHOD,
                        }
                    )
                event_count += len(frame)
                if event_count > 4000:
                    raise ValueError(
                        "Study exceeds 4000 observations; reduce locations or duration"
                    )
        observations.sort(
            key=lambda o: (o.get("start", o["epoch"]), o.get("end", ""), o["satellite"])
        )
        result["points"].append(
            {
                **study_point.model_dump(),
                "id": index,
                "observations": observations,
                **(
                    summarize_events(observations, accounting_method)
                    if event_based
                    else summarize_intervals(
                        observations, request.start_time, request.end_time
                    )
                ),
            }
        )
    if include_tracks:
        pointed = sum(
            isinstance(i, PointedInstrument) for s in satellites for i in s.instruments
        )
        samples = len(
            sample_times(
                request.start_time, request.end_time, request.display_step_seconds
            )
        )
        if pointed * samples > MAX_FOOTPRINT_SAMPLES:
            raise ValueError(
                f"Footprints of {pointed} pointed instruments at {samples} display samples "
                "exceed the scene limit; increase display_step_seconds or shorten the window"
            )
        result["tracks"] = [
            _display_track(satellite, request) for satellite in satellites
        ]
    # Fail explicitly if an upstream numerical result was not JSON-finite.
    json.dumps(result, allow_nan=False)
    return result


def _display_track(satellite, request):
    from tatc.schemas import PointedInstrument

    args = (request.start_time, request.end_time, request.display_step_seconds)
    track = sample_orbit(satellite.name, satellite.orbit, *args)
    pointed = [i for i in satellite.instruments if isinstance(i, PointedInstrument)]
    if pointed:
        orbit_track = satellite.orbit.to_gp_orbit().get_orbit_track(sample_times(*args))
        track["footprints"] = [_footprints(i, orbit_track) for i in pointed]
    return track


def _footprints(instrument, orbit_track):
    """TATC footprint and boresight ground point at each display sample."""
    # number_points counts points per side of a rectangle, or around an ellipse.
    shapes = instrument.compute_footprint(
        orbit_track, number_points=2 if instrument.is_rectangular else 16
    )
    centers = instrument.compute_footprint_center(orbit_track)
    return {
        "instrument": instrument.name,
        # A footprint crossing the antimeridian is split into several parts.
        "parts": [
            [
                [[round(x, 3), round(y, 3)] for x, y, *_ in part.exterior.coords]
                for part in getattr(shape, "geoms", [shape])
            ]
            for shape in shapes
        ],
        "centers": [
            [round(float(lon), 3), round(float(lat), 3)]
            for lon, lat in zip(centers.longitude.degrees, centers.latitude.degrees)
        ],
    }


def sample_times(start_time, end_time, step_seconds):
    times = []
    current = start_time
    while current < end_time:
        times.append(current)
        current += timedelta(seconds=step_seconds)
    times.append(end_time)
    return times


def sample_orbit(name, orbit, start_time, end_time, step_seconds):
    """Sample a complete trajectory; reject failed or non-finite propagation."""
    from skyfield.api import wgs84

    times = sample_times(start_time, end_time, step_seconds)
    positions = wgs84.subpoint(orbit.to_gp_orbit().get_orbit_track(times))
    samples = []
    for time, lon, lat, alt in zip(
        times,
        positions.longitude.degrees,
        positions.latitude.degrees,
        positions.elevation.m,
        strict=True,
    ):
        if not all(math.isfinite(float(v)) for v in (lon, lat, alt)):
            raise ValueError("Non-finite orbit position")
        samples.append([timestamp(time), float(lon), float(lat), float(alt)])
    return {"name": name, "samples": samples}
