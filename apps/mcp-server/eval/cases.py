"""Coverage conversations the eval asks every model, with their checks.

Each case is one researcher request. Checks read the trace of tool calls
and the final reply, and look at what the server actually computed (the
saved summary), not at what the model claims. Prompts use dates relative
to the eval's pinned "now" so a replayed cache keeps working.
"""

import copy
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

FINISH_TOOLS = {
    "finish_access_run",
    "finish_short_access_run",
    "finish_year_access_run",
    "run_satellite_access",
}


def _load_study_config():
    configuration = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "data"
            / "configurations"
            / "amsr2-gcom-w1-conical-product-sector-2026-10-27.1.json"
        ).read_text()
    )
    configuration["configuration_id"] = "amsr2-gcom-w1-conical-product-sector-study"
    configuration["revision"] = "2026-10-27.1-study"
    for check in configuration["geometry_checks"]:
        check.pop("geometry_parameter", None)
    return configuration


CONICAL_CONFIG = _load_study_config()
CONICAL_CONFIG["configuration_id"] += "-eval"
CONICAL_SHARED_ID = "amsr2-gcom-w1-conical-product-sector"
CONICAL_SHARED_REVISION = "2026-10-27.1"

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
    geometry = summary.get("geometry_model") or {}
    if geometry.get("geometry_input_kind") == kind:
        reported = geometry.get("geometry_input_value")
        return reported is not None and abs(float(reported) - value) <= 0.005 * value
    if kind == "swath_width_km":
        # A field of regard the converter produced from the right swath is fine.
        modeled = geometry.get("modeled_swath_width_km_at_omm_mean_altitude")
        return modeled is not None and abs(float(modeled) - value) <= 0.01 * value
    return False


def _base_checks(summary, seen, points, max_span, window_ok, kind, value, status):
    if summary is None:
        return {
            "completed": False,
            "window": False,
            "region": False,
            "geometry": False,
            "sourced": False,
            "assessment": False,
        }
    start, duration = _window(summary)
    geometry = summary.get("geometry_model") or {}
    assessment = summary.get("geometry_assessment", {})
    return {
        "completed": True,
        "window": window_ok(start, duration),
        "region": _contains(summary, points, max_span),
        "geometry": _geometry(summary, kind, value),
        "sourced": _sourced(geometry.get("geometry_input_value"), seen),
        "assessment": assessment.get("status") == status
        and _sourced(
            geometry.get("geometry_input_value"),
            [assessment.get("source_text", "")],
        ),
    }


def _given(points, max_span, kind, value, window, status="supported"):
    def checks(trace: Trace, clock: Clock) -> Dict[str, bool]:
        summary, seen = successful_finish(trace)
        return _base_checks(
            summary, seen, points, max_span,
            lambda start, duration: window(clock, start, duration), kind, value, status,
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


def _needs_clarification(source_terms: Tuple[str, ...]):
    """Require a relevant source lookup and a question before any run."""

    def checks(trace: Trace, clock: Clock) -> Dict[str, bool]:
        del clock
        summary, _ = successful_finish(trace)
        source_lookup = any(
            not call.error
            and call.name
            in {"get_satellite_instruments", "get_instrument_info", "resolve_instrument_geometry"}
            and any(term in str(call.args).lower() for term in source_terms)
            for call in trace.calls
        )
        text = trace.final.lower()
        asked_about_geometry = "?" in trace.final and any(
            word in text for word in ("swath", "coverage", "geometry", "mode", "angle")
        )
        return {
            "did_not_run": summary is None,
            "looked_up_relevant_source": source_lookup,
            "asked_about_geometry": asked_about_geometry,
        }

    return checks


def _lookup_result(points, max_span, kind, value, window, instrument):
    """Require the selected number and its evidence to come from a lookup."""

    def checks(trace: Trace, clock: Clock) -> Dict[str, bool]:
        result = _given(points, max_span, kind, value, window)(trace, clock)
        summary, _ = successful_finish(trace)
        assessment = summary.get("geometry_assessment", {}) if summary else {}
        source_text = assessment.get("source_text", "")

        def source_matches(call: Call) -> bool:
            try:
                payload = json.loads(call.text)
            except json.JSONDecodeError:
                return False
            candidates = payload.get("geometry_candidates", []) if isinstance(payload, dict) else []

            def candidate_matches(candidate: Any) -> bool:
                if (
                    not isinstance(candidate, dict)
                    or candidate.get("kind") != kind
                    or not _sourced(value, [str(candidate.get("value", ""))])
                ):
                    return False
                candidate_text = candidate.get("source_text")
                if not isinstance(candidate_text, str):
                    return False
                field_label = candidate.get("source_field")
                excerpt = source_text.removeprefix(f"{field_label}: ")
                return bool(excerpt) and excerpt in candidate_text

            return isinstance(candidates, list) and any(
                candidate_matches(candidate) for candidate in candidates
            )

        finish_index = next(
            (
                index
                for index, call in enumerate(trace.calls)
                if call.name in FINISH_TOOLS
                and not call.error
                and call.parsed
                and call.parsed.get("result_id")
            ),
            len(trace.calls),
        )
        looked_up = any(
            call.name == "resolve_instrument_geometry"
            and not call.error
            and instrument in str(call.args).lower()
            and source_matches(call)
            for call in trace.calls[:finish_index]
        )
        result["looked_up_geometry_source"] = looked_up
        return result

    return checks


def _approximate_result(points, max_span, kind, value, window):
    def checks(trace: Trace, clock: Clock) -> Dict[str, bool]:
        result = _given(points, max_span, kind, value, window, status="approximate")(
            trace, clock
        )
        summary, _ = successful_finish(trace)
        assessment = summary.get("geometry_assessment", {}) if summary else {}
        final = trace.final.lower()
        filled_envelope = "fill" in final and "envelope" in final
        approximation = any(
            cue in final for cue in ("approximat", "simplif")
        ) or filled_envelope
        limitation = any(cue in final for cue in ("gap", "track"))
        result.update(
            has_warning=isinstance(assessment.get("warning"), str)
            and bool(assessment["warning"].strip()),
            reported_warning=approximation and limitation,
        )
        return result

    return checks


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

def successful_conical_finish(trace: Trace):
    """Last conical finish that returned a result, and its parsed summary."""
    for call in reversed(trace.calls):
        if (
            call.name == "finish_conical_study"
            and not call.error
            and call.parsed
            and call.parsed.get("result_id")
        ):
            return call
    return None


def _conical_reporting_is_limited(final: str) -> bool:
    """Check explicit sampling limits and reject common contradictory claims.

    These text checks are conservative heuristics, not a semantic assessment
    of every possible explanation.
    """
    negation = (
        r"\b(?:not|never|no|cannot|can't|couldn't|wouldn't|shouldn't|doesn't|"
        r"didn't|isn't|aren't|wasn't|weren't|hasn't|haven't|hadn't|don't|"
        r"does not|do not|did not|is not|are not|was not|were not|no longer)\b"
    )
    continuous = r"\b(?:continuous|continuously|uninterrupted)\b"
    sampling = re.search(
        r"\bcell[- ]cent(?:er|re)s?\b|"
        r"\b(?:event|visit|sample)\w*\b[^.!?\n]*\bcent(?:er|re)s?\b", final
    )
    continuous_limit = (
        negation + r".{0,80}" + continuous + "|" + continuous
        + r".{0,120}\b(?:unavailable|not available|not provided|not computed|"
        + r"(?:isn't|aren't|wasn't|weren't)\s+available)\b"
    )
    for clause in re.split(r"[.!?;\n]|\b(?:but|however|yet|although|whereas)\b", final):
        if re.search(continuous, clause) and not re.search(continuous_limit, clause):
            return False
        whole_cell = re.search(
            r"\b(?:every|all) (?:point|pixel|location)s?\b.*\bcell\w*\b|"
            r"\b(?:whole|entire|full) (?:grid[- ])?cells?\b", clause
        )
        classification = re.search(
            r"\b(?:classif\w*|counted|counts? as|based on|described as|"
            r"labell?ed|marked as)\b",
            clause,
        )
        if (
            whole_cell
            and re.search(r"\b(?:observed|viewed|covered|scanned)\b", clause)
            and not classification
            and not re.search(negation, clause)
        ):
            return False
    return bool(sampling and "envelope" in final and re.search(continuous_limit, final))


def _conical_complete(expected_config_id: str):
    def checks(trace: Trace, clock: Clock) -> Dict[str, bool]:
        finish = successful_conical_finish(trace)
        if finish is None or not finish.parsed:
            return {
                "completed": False,
                "prepared": False,
                "accepted_revision": False,
                "event_count": False,
                "window": False,
                "region": False,
                "source_choice": False,
                "provenance": False,
                "fetched": False,
                "reported": False,
            }
        summary = finish.parsed
        result = {
            "completed": summary.get("result_kind") == "conical_study",
            "prepared": any(
                call.name == "prepare_conical_study" and not call.error
                for call in trace.calls
            ),
            "accepted_revision": bool(
                isinstance(finish.args.get("accepted_setup_revision"), str)
                and finish.args.get("accepted_setup_revision") == summary.get("setup_revision")
                and finish.args.get("accept_setup") is True
            ),
            "event_count": summary.get("summary_counts", {}).get("event_count", 0) > 0,
            "window": summary.get("window_start_utc")
            == clock.d0.isoformat().replace("+00:00", "Z")
            and summary.get("window_end_utc_exclusive")
            == (clock.d0 + timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
            "region": _contains(summary, [(-60.0, 5.0)], 20.0),
            "source_choice": any(
                call.name == "prepare_conical_study"
                and not call.error
                and isinstance(call.parsed, dict)
                and any(
                    conflict.get("chosen_option_id") == "product-75deg"
                    for conflict in call.parsed.get("source_conflicts", [])
                    if isinstance(conflict, dict)
                )
                for call in trace.calls
            ),
            "provenance": (
                summary.get("provenance", {}).get("setup_revision")
                == summary.get("setup_revision")
                and summary.get("configuration_id") == expected_config_id
            ),
            "fetched": any(
                call.name in {"get_access_events", "get_access_daily", "get_access_cells"}
                and not call.error
                for call in trace.calls
            ),
        }
        final = trace.final.lower()
        event_count = summary.get("summary_counts", {}).get("event_count", 0)
        result["reported"] = (
            str(event_count) in re.findall(r"\d+", final)
            and _conical_reporting_is_limited(final)
        )
        return result

    return checks


def _conical_registry_complete(expected_config_id: str, expected_revision: str):
    base_checks = _conical_complete(expected_config_id)

    def checks(trace: Trace, clock: Clock) -> Dict[str, bool]:
        result = base_checks(trace, clock)
        lookup_calls = [
            (index, call)
            for index, call in enumerate(trace.calls)
            if call.name == "lookup_instrument_configurations"
            and not call.error
            and call.parsed
        ]
        prepare_calls = [
            (index, call)
            for index, call in enumerate(trace.calls)
            if call.name == "prepare_conical_study" and not call.error and call.parsed
        ]
        finish = successful_conical_finish(trace)
        matching_lookup = any(
            index < prepare_index
            and call.parsed.get("selection_required") is True
            and call.parsed.get("selection") is None
            and any(
                candidate.get("configuration_id") == expected_config_id
                and candidate.get("revision") == expected_revision
                and candidate.get("registry_status") == "reviewed_shared"
                for candidate in call.parsed.get("candidates", [])
            )
            for index, call in lookup_calls
            for prepare_index, _ in prepare_calls
        )
        explicit_prepare = any(
            call.args.get("configuration_id") == expected_config_id
            and call.args.get("configuration_revision") == expected_revision
            and call.args.get("configuration") is None
            and call.parsed.get("registry_selection", {}).get("selection")
            == "explicit_exact_revision"
            for _, call in prepare_calls
        )
        result["looked_up_candidate_without_selection"] = matching_lookup
        result["prepared_exact_registry_revision"] = explicit_prepare
        result["result_keeps_registry_revision"] = bool(
            finish
            and finish.parsed.get("registry_selection", {}).get("configuration_id")
            == expected_config_id
            and finish.parsed.get("registry_selection", {}).get("revision")
            == expected_revision
        )
        return result

    return checks


def _conical_registry_failed_refresh(expected_config_id: str, expected_revision: str):
    base_checks = _conical_registry_complete(expected_config_id, expected_revision)

    def checks(trace: Trace, clock: Clock) -> Dict[str, bool]:
        result = base_checks(trace, clock)
        failure_calls = [
            (index, call)
            for index, call in enumerate(trace.calls)
            if call.name == "record_instrument_configuration_refresh"
            and not call.error
            and call.parsed
            and call.parsed.get("status") == "failed"
        ]
        prepares = [
            (index, call)
            for index, call in enumerate(trace.calls)
            if call.name == "prepare_conical_study"
            and not call.error
            and call.parsed
        ]
        finish = successful_conical_finish(trace)
        uncertainty = (
            finish.parsed.get("registry_selection", {}).get("refresh_uncertainty", [])
            if finish and finish.parsed
            else []
        )
        result["recorded_failed_refresh_before_prepare"] = any(
            failure_index < prepare_index
            for failure_index, _ in failure_calls
            for prepare_index, _ in prepares
        )
        result["result_retains_refresh_uncertainty"] = bool(
            uncertainty
            and uncertainty[0].get("status") == "failed"
            and uncertainty[0].get("failure_reason")
        )
        final = trace.final.lower()
        reports_uncertainty = any(
            term in final
            for term in ("unknown", "could not be checked", "source status is uncertain")
        )
        affirmative_unchanged = False
        clauses = re.split(r"[.!?;]|\b(?:but|however|yet)\b|,\s*and\b", final)
        for clause in clauses:
            source_subject = (
                r"\b(?:source(?: status| state)?|sources?|source evidence|geometry)\b"
            )
            explicit_no_change = re.search(
                source_subject
                + r"[^.!?]{0,100}\b(?:has|have|had|did|does|is|are|was|were|remain|remains)"
                + r"(?:\s+(?:not|never)|n't)\s+change(?:d)?\b",
                clause,
            )
            unchanged = re.search(
                source_subject + r"[^.!?]{0,100}\b(unchanged|no changes?)\b",
                clause,
            )
            if explicit_no_change:
                affirmative_unchanged = True
                break
            if unchanged:
                before = clause[: unchanged.start(1)]
                negated_claim = re.search(
                    r"\b(?:no|without)\s+claim\b[^.!?]{0,120}$"
                    r"|\bnot\s+an?\s+claim\b[^.!?]{0,120}$"
                    r"|\b(?:no|without)\s+(?:evidence|indication|proof|sign)"
                    r"\s+that\b[^.!?]{0,120}$"
                    r"|\b(?:does|do|did|is|are|was|were|has|have|had)\s+not\s+"
                    r"(?:claim|confirm|establish|show|prove|mean|indicate|say|"
                    r"state)\b[^.!?]{0,120}$"
                    r"|\b(?:cannot|can't|couldn't)\s+(?:claim|confirm|establish|"
                    r"show|prove|mean|indicate|say|state)\b[^.!?]{0,120}$",
                    before,
                )
                local_negation = re.search(
                    r"\b(?:not|no|never|nothing|none|nobody|neither|cannot|can't|"
                    r"couldn't|wouldn't|shouldn't|doesn't|didn't|isn't|aren't|"
                    r"wasn't|weren't|hasn't|haven't|hadn't|no longer)\b"
                    r"[^.!?]{0,40}$",
                    before,
                )
                if not (negated_claim or local_negation):
                    affirmative_unchanged = True
                    break
        result["reported_source_uncertainty"] = (
            reports_uncertainty and not affirmative_unchanged
        )
        return result

    return checks


def _conical_rejected(cue_terms: Tuple[str, ...]):
    def checks(trace: Trace, clock: Clock) -> Dict[str, bool]:
        del clock
        finish = successful_conical_finish(trace)
        text = trace.final.lower()
        call_text = " ".join(call.text.lower() for call in trace.calls if call.error)
        return {
            "did_not_finish": finish is None and successful_finish(trace)[0] is None,
            "explained": any(term in text or term in call_text for term in cue_terms),
        }

    return checks


def _with_applicability_end(config: Dict[str, Any], end: datetime) -> Dict[str, Any]:
    data = copy.deepcopy(config)
    data["applicability"]["end_utc_exclusive"] = end.isoformat().replace("+00:00", "Z")
    return data


def _with_unresolved_sector(config: Dict[str, Any]) -> Dict[str, Any]:
    data = copy.deepcopy(config)
    for conflict in data["source_conflicts"]:
        if conflict["conflict_id"] == "scan-sector-nominal-vs-product":
            conflict["chosen_option_id"] = None
            conflict["rationale"] = None
    return data


def _without_validation_evidence(config: Dict[str, Any]) -> Dict[str, Any]:
    data = copy.deepcopy(config)
    data["evidence"] = [
        item for item in data["evidence"] if item["evidence_id"] != "tatc-amsr2-validation"
    ]
    data["source_revision"]["evidence_ids"] = [
        item for item in data["source_revision"]["evidence_ids"]
        if item != "tatc-amsr2-validation"
    ]
    return data



def _conical_unresolved():
    def checks(trace: Trace, clock: Clock) -> Dict[str, bool]:
        del clock
        conical_finish = successful_conical_finish(trace)
        legacy_finish = successful_finish(trace)[0] is not None
        successful_prepare = any(
            call.name == "prepare_conical_study" and not call.error
            for call in trace.calls
        )
        final = trace.final.lower()
        asked = "?" in trace.final and any(
            term in final for term in ("sector", "nominal", "product")
        )
        silent_choice = any(
            phrase in final
            for phrase in (
                "i choose",
                "i chose",
                "i picked",
                "i selected",
                "i'll use",
                "i will use",
            )
        )
        return {
            "did_not_finish": conical_finish is None and not legacy_finish,
            "no_invented_choice": not successful_prepare,
            "asked_for_choice": asked,
            "no_silent_choice": not silent_choice,
        }

    return checks


def build_cases(clock: Clock) -> List[Case]:
    d0 = clock.fmt(clock.d0)
    modis = (
        "Study source excerpt: 'A full field of regard of 110 degrees represents "
        "a continuous nadir-centered swath with no gaps or separate pointing "
        "needed.' This is synthetic fixture text for the study, not an official "
        "instrument-source claim."
    )
    laser_tracks = (
        "Study source excerpt: 'Six separated laser tracks with gaps have a "
        "documented overall swath span of 6.6 km.' This is synthetic fixture "
        "text for the study, not an official instrument-source claim."
    )
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
            "needs_clarification", ("geometry_unclear",),
            "How often can the radar on Sentinel-1A see Phoenix, Arizona over the next 2 days?",
            _needs_clarification(("sentinel-1a", "c-sar", "sar")),
        ),
        Case(
            "resolver_lookup_supported", ("short", "geometry_lookup"),
            f"Look up MODIS with the geometry resolver and use its returned swath width "
            f"to estimate Terra access over Phoenix for 24 hours starting {d0}. For this "
            "synthetic study assumption, treat that selected width as continuous, "
            "nadir-centered coverage with no gaps or separate pointing needed. The width "
            "must come from the resolver result, not memory or this prompt.",
            _lookup_result(
                [PHOENIX], 5.0, "swath_width_km", 2230.0, _exact(1), "modis"
            ),
        ),
        Case(
            "fetch_gaps", ("short", "geometry_given", "fetch"),
            f"Estimate Landsat 9 access over Colorado for 24 hours starting {d0}, using its "
            "185 km swath. For this synthetic study assumption, the researcher-supplied "
            "geometry evidence describes a continuous nadir-centered swath with no gaps "
            "or separate pointing needed. This is an evaluation assumption, not a claim "
            "about official Landsat documentation. "
            "Which parts of the state were never observed?",
            _gap_cells,
        ),
        Case(
            "separated_tracks_approximation",
            ("short", "geometry_approximate"),
            f"Estimate Terra access over Phoenix, Arizona for 24 hours starting {d0}, "
            f"using this study source excerpt: {laser_tracks} The model will use a "
            "filled envelope for the overall span. Include that limitation in your "
            "final summary.",
            _approximate_result([PHOENIX], 5.0, "swath_width_km", 6.6, _exact(1)),
        ),
        Case(
            "conical_amsr2_complete",
            ("conical", "complete", "source_conflict", "approximate"),
            f"Run a conical AMSR2 study for GCOM-W1 over the box west=0, south=-65, "
            f"east=10, north=-55 with a 2x2 grid, from {d0} for 2 hours. "
            "Use this exact sourced configuration; do not add, round, or change "
            "its angles, frame, dates, evidence, source choices, or approximations. "
            "I have reviewed this configuration and its declared approximations and "
            "source choices, and I accept them. Prepare the study, show the returned "
            "consequential choices and warnings, and if the proposal matches this "
            "configuration, run it with the returned setup revision and accept_setup=true "
            "without asking me again. Then fetch and summarize the viewing events. "
            "Explain that daily area is cell-center sampled and that field-of-regard "
            "envelopes are not continuous viewing time. "
            f"Configuration JSON: {json.dumps(CONICAL_CONFIG, separators=(',', ':'))}",
            _conical_complete(CONICAL_CONFIG["configuration_id"]),
        ),
        Case(
            "conical_amsr2_registry_selection",
            ("conical", "registry", "lookup", "revision", "complete"),
            f"Run a conical AMSR2 study for GCOM-W1 over west=0, south=-65, "
            f"east=10, north=-55 with a 2x2 grid, from {d0} for 2 hours. Use "
            "the shared configuration lookup tool. I explicitly select the "
            f"reviewed configuration revision {CONICAL_SHARED_REVISION} for the "
            "product scan sector and accept its stated approximations and source "
            "choices. Use the exact ID and revision returned by lookup. Prepare "
            "the study, show me its warnings and settings, run it with the exact "
            "accepted setup revision, then fetch the viewing events. Explain "
            "cell-center sampling and that field-of-regard envelopes are not "
            "continuous viewing time.",
            _conical_registry_complete(CONICAL_SHARED_ID, CONICAL_SHARED_REVISION),
        ),
        Case(
            "conical_amsr2_failed_refresh",
            ("conical", "registry", "refresh", "uncertainty"),
            f"Run a conical AMSR2 study for GCOM-W1 over west=0, south=-65, "
            f"east=10, north=-55 with a 2x2 grid, from {d0} for 2 hours. "
            "I tried to refresh the JAXA source page on 2026-10-28, but it was "
            "unavailable. Record a failed source refresh for the reviewed AMSR2 "
            f"configuration at revision {CONICAL_SHARED_REVISION}, with source "
            "check ID jaxa-page-attempt-2026-10-28 and failure reason that the "
            "page was unavailable. You may look up the configuration first to "
            "discover its exact ID. Record the failed refresh before preparing, "
            "then use the exact "
            f"revision {CONICAL_SHARED_REVISION}, which I explicitly select for "
            "this historical window and accept with the source-status warning. "
            "Prepare, run with the accepted setup revision, and fetch the events. "
            "State that the failed check leaves current source status unknown. "
            "Report the event count, say that daily area is cell-center sampled, "
            "and explain that the field-of-regard envelope is not continuous "
            "viewing time.",
            _conical_registry_failed_refresh(CONICAL_SHARED_ID, CONICAL_SHARED_REVISION),
        ),
        Case(
            "conical_applicability_rejected",
            ("conical", "applicability", "reject"),
            f"Run a conical AMSR2 study for GCOM-W1 over west=0, south=-65, east=10, "
            f"north=-55 with a 2x2 grid from {d0} for 2 hours. Use this exact "
            "configuration, but its established applicability ends before the window. "
            "Do not invent or extend dates; if the window is unsupported, stop before "
            "calculation and tell me which part is uncovered. "
            f"Configuration JSON: {json.dumps(_with_applicability_end(CONICAL_CONFIG, clock.d0 + timedelta(hours=1)), separators=(',', ':'))}",
            _conical_rejected(("applicab", "outside", "not covered", "uncovered", "date")),
        ),
        Case(
            "conical_unresolved_sector",
            ("conical", "source_conflict", "mode_selection", "reject"),
            f"Run a conical AMSR2 study for GCOM-W1 over west=0, south=-65, east=10, "
            f"north=-55 with a 2x2 grid from {d0} for 2 hours. I have not chosen "
            "between the nominal performance scan sector and the wider product "
            "sector, and this configuration has no recorded choice or rationale. "
            "Do not guess or choose for me. Ask me which sector to use before any "
            "calculation. "
            f"Configuration JSON: {json.dumps(_with_unresolved_sector(CONICAL_CONFIG), separators=(',', ':'))}",
            _conical_unresolved(),
        ),
        Case(
            "conical_missing_evidence",
            ("conical", "missing_evidence", "reject"),
            f"Run a conical AMSR2 study for GCOM-W1 over west=0, south=-65, east=10, "
            f"north=-55 with a 2x2 grid from {d0} for 2 hours. I only have this "
            "incomplete configuration; its measured cone and sector evidence is "
            "missing. Do not invent evidence or substitute a nominal swath. If the "
            "required evidence is missing, stop before calculation and ask me for it. "
            f"Configuration JSON: {json.dumps(_without_validation_evidence(CONICAL_CONFIG), separators=(',', ':'))}",
            _conical_rejected(("evidence", "source", "missing", "cannot")),
        ),
    ]
