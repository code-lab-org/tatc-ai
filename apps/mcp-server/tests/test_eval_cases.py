"""Offline checks for geometry behavior scored by the tool-calling eval."""

from datetime import datetime, timezone

from eval.cases import Call, Clock, Trace, build_cases


def _clock():
    return Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))


def _approximation_trace(final):
    clock = _clock()
    start = clock.d0
    end = start.replace(day=start.day + 1)
    summary = {
        "result_id": "result-1",
        "window_start_utc": start.isoformat(),
        "window_end_utc_exclusive": end.isoformat(),
        "roi_bbox": [-113.0, 32.0, -111.0, 35.0],
        "geometry_model": {
            "geometry_input_kind": "swath_width_km",
            "geometry_input_value": 6.6,
        },
        "geometry_assessment": {
            "status": "approximate",
            "source_text": "Six separated tracks span 6.6 km.",
            "warning": "The model fills gaps between the tracks.",
        },
    }
    trace = Trace(
        prompt="Study source says overall span 6.6 km.",
        calls=[
            Call(
                "finish_access_run",
                {},
                False,
                "{}",
                parsed=summary,
            )
        ],
        final=final,
    )
    return trace, clock


def test_approximation_eval_requires_a_warning_in_the_final_reply():
    case = next(
        case for case in build_cases(_clock()) if case.id == "separated_tracks_approximation"
    )
    complete, clock = _approximation_trace(
        "The model fills gaps between tracks, so the access estimate is approximate."
    )
    checks = case.checks(complete, clock)
    assert all(checks.values()), checks

    specific_reply, clock = _approximation_trace(
        "The model used a filled 6.6 km swath envelope over six separated tracks "
        "with gaps, which overstates actual coverage."
    )
    checks = case.checks(specific_reply, clock)
    assert all(checks.values()), checks

    missing_warning, clock = _approximation_trace("The access estimate is complete.")
    checks = case.checks(missing_warning, clock)
    assert checks["completed"]
    assert checks["has_warning"]
    assert not checks["reported_warning"]

    misleading, clock = _approximation_trace(
        "There are no gaps; coverage is complete."
    )
    checks = case.checks(misleading, clock)
    assert checks["has_warning"]
    assert not checks["reported_warning"]

    fabricated, clock = _approximation_trace(
        "The model used a filled 6.6 km swath envelope over tracks with gaps."
    )
    fabricated.prompt = "The source mentions separated tracks, but gives no span."
    fabricated.calls[0].parsed["geometry_assessment"]["source_text"] = (
        "Six separated tracks have gaps."
    )
    checks = case.checks(fabricated, clock)
    assert checks["reported_warning"]
    assert not checks["sourced"]
    assert not checks["assessment"]
    assert not all(checks.values())


def test_clarification_eval_requires_source_lookup_and_no_completed_run():
    case = next(case for case in build_cases(_clock()) if case.id == "needs_clarification")
    trace = Trace(
        prompt=case.prompt,
        calls=[
            Call(
                "get_satellite_instruments",
                {"satellite_identifier": "Sentinel-1A"},
                False,
                "Source instrument metadata.",
            )
        ],
        final="Which geometry or swath should we use for this estimate?",
    )
    checks = case.checks(trace, _clock())
    assert all(checks.values()), checks

    no_lookup = Trace(
        prompt=case.prompt,
        final="Which geometry or swath should we use for this estimate?",
    )
    checks = case.checks(no_lookup, _clock())
    assert checks["did_not_run"]
    assert checks["asked_about_geometry"]
    assert not checks["looked_up_relevant_source"]

    irrelevant_lookup = Trace(
        prompt=case.prompt,
        calls=[
            Call(
                "get_instrument_info",
                {"instrument_identifier": "Venus"},
                False,
                "Venus metadata.",
            )
        ],
        final="Which geometry or swath should we use for this estimate?",
    )
    checks = case.checks(irrelevant_lookup, _clock())
    assert checks["did_not_run"]
    assert not checks["looked_up_relevant_source"]
    assert not all(checks.values())

    completed = _approximation_trace("The run is complete.")[0]
    completed.prompt = case.prompt
    completed.calls.insert(
        0,
        Call(
            "get_instrument_info",
            {"instrument_identifier": "Sentinel-1A"},
            False,
            "Sentinel-1A source metadata.",
        ),
    )
    checks = case.checks(completed, _clock())
    assert checks["looked_up_relevant_source"]
    assert not checks["did_not_run"]
    assert not all(checks.values())


def _supported_lookup_trace(include_lookup=True):
    clock = _clock()
    start = clock.d0
    end = start.replace(day=start.day + 1)
    summary = {
        "result_id": "result-2",
        "window_start_utc": start.isoformat(),
        "window_end_utc_exclusive": end.isoformat(),
        "roi_bbox": [-113.0, 32.0, -111.0, 35.0],
        "geometry_model": {
            "geometry_input_kind": "swath_width_km",
            "geometry_input_value": 2230.0,
        },
        "geometry_assessment": {
            "status": "supported",
            "source_text": "Scanning Technique: Swath 2230 km.",
            "explanation": "The study assumes continuous nadir-centered coverage.",
        },
    }
    calls = []
    if include_lookup:
        calls.append(
            Call(
                "resolve_instrument_geometry",
                {"instrument_identifier": "MODIS"},
                False,
                '{"geometry_candidates":[{"kind":"swath_width_km",'
                '"value":2230.0,"source_field":"Scanning Technique",'
                '"source_text":"Swath 2230 km."}]}',
            )
        )
    calls.append(Call("finish_access_run", {}, False, "{}", parsed=summary))
    return Trace(
        prompt=(
            "Look up MODIS. For this synthetic study, assume the selected swath "
            "is continuous and nadir-centered. Estimate Terra access over Phoenix "
            "for 24 hours starting 2026-01-02T00:00:00Z."
        ),
        calls=calls,
        final="The requested access estimate is complete.",
    ), clock


def test_lookup_resolution_requires_width_from_a_relevant_source_call():
    case = next(
        case for case in build_cases(_clock()) if case.id == "resolver_lookup_supported"
    )
    sourced, clock = _supported_lookup_trace()
    checks = case.checks(sourced, clock)
    assert all(checks.values()), checks

    no_lookup, clock = _supported_lookup_trace(include_lookup=False)
    checks = case.checks(no_lookup, clock)
    assert checks["completed"]
    assert not checks["looked_up_geometry_source"]
    assert not all(checks.values())

    fabricated, clock = _supported_lookup_trace()
    fabricated.calls[-1].parsed["geometry_assessment"]["source_text"] = (
        "Scanning Technique: Swath 2230 km; continuous nadir support."
    )
    checks = case.checks(fabricated, clock)
    assert checks["sourced"]
    assert checks["assessment"]
    assert not checks["looked_up_geometry_source"]
    assert not all(checks.values())


def test_lookup_resolution_accepts_a_faithful_excerpt_of_the_candidate():
    case = next(
        case for case in build_cases(_clock()) if case.id == "resolver_lookup_supported"
    )
    trace, clock = _supported_lookup_trace()
    trace.calls[0].text = (
        '{"geometry_candidates":[{"kind":"swath_width_km","value":2230.0,'
        '"source_field":"Scanning Technique","source_text":"Swath 2230 km. '
        "Whiskbroom scanning: a strip of 19.7 km width along-track is cross-track "
        'scanned every 2.956 s. The strip includes 16 parallel lines."}]}'
    )
    trace.calls[-1].parsed["geometry_assessment"]["source_text"] = (
        "Scanning Technique: Swath 2230 km. Whiskbroom scanning: a strip of 19.7 km "
        "width along-track is cross-track scanned every 2.956 s."
    )

    checks = case.checks(trace, clock)
    assert all(checks.values()), checks
