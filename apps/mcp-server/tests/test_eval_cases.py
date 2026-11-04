"""Offline checks for geometry behavior scored by the tool-calling eval."""

import copy
from datetime import datetime, timedelta, timezone

from eval.cases import (
    CONICAL_CONFIG,
    Call,
    Clock,
    Trace,
    build_cases,
)


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


def _short_point_trace(final="2 observations"):
    clock = _clock()
    start = clock.d0
    end = start + timedelta(days=1)
    summary = {
        "analysis_id": "point-1",
        "scenario": {
            "satellites": [
                {
                    "name": "TERRA",
                    "instruments": [
                        {"name": "MODIS", "field_of_regard": 110.0}
                    ],
                    "orbit": {
                        "type": "gp",
                        "elements": [
                            {
                                "norad_cat_id": 25994,
                                "epoch": "2026-10-09T07:30:04.256064Z",
                            }
                        ],
                    },
                }
            ],
            "points": [
                {
                    "name": "Phoenix",
                    "latitude": 33.4484,
                    "longitude": -112.074,
                    "elevation": 0,
                }
            ],
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
        },
        "points": [
            {
                "metrics": {
                    "observation_count": 2,
                    "covered_seconds": 520.0,
                    "coverage_fraction": 0.006,
                    "longest_gap_seconds": 3600.0,
                }
            }
        ],
    }
    trace = Trace(
        prompt="Estimate Terra access over Phoenix for 24 hours using 110 degrees.",
        calls=[
            Call(
                "get_satellite_info",
                {"satellite_identifier": "Terra"},
                False,
                '{"norad_id":25994,"omm":[{"EPOCH":"2026-10-09T07:30:04.256064"}]}',
            ),
            Call("analyze_coverage", {"scenario": {}}, False, "{}", parsed=summary),
        ],
        final=final,
    )
    return trace, clock


def _short_legacy_trace():
    clock = _clock()
    start = clock.d0
    end = start + timedelta(days=1)
    summary = {
        "result_id": "legacy-1",
        "window_start_utc": start.isoformat(),
        "window_end_utc_exclusive": end.isoformat(),
        "roi_bbox": [-113.0, 32.0, -111.0, 35.0],
        "geometry_model": {
            "geometry_input_kind": "field_of_regard_deg",
            "geometry_input_value": 110.0,
        },
        "geometry_assessment": {
            "status": "supported",
            "source_text": "A full field of regard of 110 degrees.",
        },
    }
    trace = Trace(
        prompt="Estimate Terra access over Phoenix for 24 hours using 110 degrees.",
        calls=[Call("finish_access_run", {}, False, "{}", parsed=summary)],
        final="The estimate is complete.",
    )
    return trace, clock


def test_short_given_accepts_a_strict_point_study_or_the_legacy_region():
    case = next(
        case for case in build_cases(_clock()) if case.id == "short_given"
    )

    point, clock = _short_point_trace()
    assert all(case.checks(point, clock).values()), case.checks(point, clock)

    legacy, clock = _short_legacy_trace()
    assert all(case.checks(legacy, clock).values()), case.checks(legacy, clock)


def test_short_given_point_path_rejects_wrong_location_geometry_window_or_orbit():
    case = next(
        case for case in build_cases(_clock()) if case.id == "short_given"
    )

    location, clock = _short_point_trace()
    location.calls[1].parsed["scenario"]["points"][0]["latitude"] = 40.0
    assert not case.checks(location, clock)["region"]

    geometry, clock = _short_point_trace()
    geometry.calls[1].parsed["scenario"]["satellites"][0]["instruments"][0][
        "field_of_regard"
    ] = 100.0
    assert not case.checks(geometry, clock)["geometry"]

    window, clock = _short_point_trace()
    window.calls[1].parsed["scenario"]["end_time"] = (
        clock.d0 + timedelta(days=2)
    ).isoformat()
    assert not case.checks(window, clock)["window"]

    invented, clock = _short_point_trace()
    invented.calls[1].parsed["scenario"]["satellites"][0]["orbit"]["elements"][0][
        "norad_cat_id"
    ] = 99999
    assert not case.checks(invented, clock)["orbit"]

    wrong_epoch, clock = _short_point_trace()
    wrong_epoch.calls[1].parsed["scenario"]["satellites"][0]["orbit"]["elements"][
        0
    ]["epoch"] = "2026-10-10T07:30:04.256064Z"
    assert not case.checks(wrong_epoch, clock)["orbit"]

    no_lookup, clock = _short_point_trace()
    no_lookup.calls.pop(0)
    assert not case.checks(no_lookup, clock)["orbit"]

    lookup_after, clock = _short_point_trace()
    lookup_after.calls.reverse()
    assert not case.checks(lookup_after, clock)["orbit"]

    by_norad, clock = _short_point_trace()
    by_norad.calls[0].args["satellite_identifier"] = "25994"
    assert all(case.checks(by_norad, clock).values())

    unreported, clock = _short_point_trace("The estimate is complete.")
    assert not case.checks(unreported, clock)["reported"]


def _explicit_point_trace(final="3 observations; longest gap 3600 s."):
    clock = _clock()
    start = clock.d0
    end = start + timedelta(hours=12)
    summary = {
        "analysis_id": "point-2",
        "scenario": {
            "satellites": [
                {
                    "name": "TERRA",
                    "instruments": [
                        {"name": "MODIS", "field_of_regard": 110.0}
                    ],
                    "orbit": {
                        "type": "gp",
                        "elements": [
                            {
                                "norad_cat_id": 25994,
                                "epoch": "2026-10-09T07:30:04.256064Z",
                            }
                        ],
                    },
                },
                {
                    "name": "LANDSAT 9",
                    "instruments": [
                        {"name": "OLI-2", "field_of_regard": 15.0}
                    ],
                    "orbit": {
                        "type": "gp",
                        "elements": [
                            {
                                "norad_cat_id": 49260,
                                "epoch": "2026-10-08T11:00:00.000000Z",
                            }
                        ],
                    },
                },
            ],
            "points": [
                {"name": "Phoenix", "latitude": 33.4484, "longitude": -112.074},
                {"name": "Tempe", "latitude": 33.4255, "longitude": -111.94},
                {"name": "Denver", "latitude": 39.7392, "longitude": -104.9903},
            ],
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
        },
        "points": [
            {
                "metrics": {
                    "observation_count": 1,
                    "covered_seconds": 100.0,
                    "longest_gap_seconds": 3600.0,
                }
            }
            for _ in range(3)
        ],
    }
    trace = Trace(
        prompt=(
            "Use analyze_coverage for Phoenix, Tempe, and Denver with 110 and "
            "15 degree fields of regard."
        ),
        calls=[
            Call(
                "get_satellite_info",
                {"satellite_identifier": "Terra"},
                False,
                '{"norad_id":25994,"omm":[{"EPOCH":"2026-10-09T07:30:04.256064"}]}',
            ),
            Call(
                "get_satellite_info",
                {"satellite_identifier": "Landsat 9"},
                False,
                '{"norad_id":49260,"omm":[{"EPOCH":"2026-10-08T11:00:00.000000"}]}',
            ),
            Call("analyze_coverage", {"scenario": {}}, False, "{}", parsed=summary),
        ],
        final=final,
    )
    return trace, clock


def test_explicit_point_study_requires_two_catalog_orbits_and_three_points():
    case = next(
        case for case in build_cases(_clock())
        if case.id == "point_study_explicit"
    )

    valid, clock = _explicit_point_trace()
    assert all(case.checks(valid, clock).values()), case.checks(valid, clock)

    missing_satellite, clock = _explicit_point_trace()
    missing_satellite.calls[2].parsed["scenario"]["satellites"].pop()
    assert not case.checks(missing_satellite, clock)["geometry"]

    duplicate_satellite, clock = _explicit_point_trace()
    duplicate_satellite.calls[2].parsed["scenario"]["satellites"][1] = copy.deepcopy(
        duplicate_satellite.calls[2].parsed["scenario"]["satellites"][0]
    )
    checks = case.checks(duplicate_satellite, clock)
    assert not checks["geometry"] and not checks["orbit"]

    missing_point, clock = _explicit_point_trace()
    missing_point.calls[2].parsed["scenario"]["points"].pop()
    assert not case.checks(missing_point, clock)["points"]

    duplicate_point, clock = _explicit_point_trace()
    duplicate_point.calls[2].parsed["scenario"]["points"][1] = copy.deepcopy(
        duplicate_point.calls[2].parsed["scenario"]["points"][0]
    )
    assert not case.checks(duplicate_point, clock)["points"]

    wrong_orbit, clock = _explicit_point_trace()
    wrong_orbit.calls[2].parsed["scenario"]["satellites"][1]["orbit"]["elements"][
        0
    ]["norad_cat_id"] = 99999
    assert not case.checks(wrong_orbit, clock)["orbit"]

    wrong_epoch, clock = _explicit_point_trace()
    wrong_epoch.calls[2].parsed["scenario"]["satellites"][1]["orbit"]["elements"][
        0
    ]["epoch"] = "2026-10-09T11:00:00.000000Z"
    assert not case.checks(wrong_epoch, clock)["orbit"]

    lookup_after, clock = _explicit_point_trace()
    lookup_after.calls.reverse()
    assert not case.checks(lookup_after, clock)["orbit"]

    named_landsat, clock = _explicit_point_trace()
    named_landsat.calls[2].parsed["scenario"]["satellites"][1]["name"] = "Landsat-9"
    assert all(case.checks(named_landsat, clock).values())

    comma, clock = _explicit_point_trace("3 observations; longest gap 3,600 s.")
    assert all(case.checks(comma, clock).values())

    partial, clock = _explicit_point_trace("3 observations.")
    assert not case.checks(partial, clock)["reported"]

    single_point, clock = _explicit_point_trace(
        "1 observation at 03:01 UTC; its longest gap was 3600 s."
    )
    assert not case.checks(single_point, clock)["reported"]

    timestamp_only, clock = _explicit_point_trace(
        "The pass was at 03:00 UTC; the longest gap was 3600 s."
    )
    assert not case.checks(timestamp_only, clock)["reported"]

    non_max_gap, clock = _explicit_point_trace()
    non_max_gap.calls[2].parsed["points"][0]["metrics"][
        "longest_gap_seconds"
    ] = 3500.0
    non_max_gap.final = "3 observations; longest gap 3500 s."
    assert not case.checks(non_max_gap, clock)["reported"]

    unreported, clock = _explicit_point_trace("The study is complete.")
    assert not case.checks(unreported, clock)["reported"]


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


def _conical_trace(clock):
    summary = {
        "result_id": "conical-1",
        "result_kind": "conical_study",
        "accounting_method": "conical_events_v1",
        "setup_revision": "sha256:abc",
        "configuration_id": CONICAL_CONFIG["configuration_id"],
        "window_start_utc": clock.d0.isoformat().replace("+00:00", "Z"),
        "window_end_utc_exclusive": (clock.d0 + __import__("datetime").timedelta(hours=2))
        .isoformat()
        .replace("+00:00", "Z"),
        "roi_bbox": [0.0, -65.0, 10.0, -55.0],
        "summary_counts": {"event_count": 4},
        "approximation_status": "approximate",
        "provenance": {"setup_revision": "sha256:abc"},
    }
    return Trace(
        prompt="Run the conical AMSR2 study.",
        calls=[
            Call(
                "prepare_conical_study",
                {"grid_rows": 2, "grid_columns": 2},
                False,
                "{}",
                parsed={
                    "setup_revision": "sha256:abc",
                    "source_conflicts": [{"chosen_option_id": "product-75deg"}],
                },
            ),
            Call(
                "finish_conical_study",
                {
                    "accepted_setup_revision": "sha256:abc",
                    "accept_setup": True,
                },
                False,
                "{}",
                parsed=summary,
            ),
            Call("get_access_events", {"result_id": "conical-1"}, False, "{}", parsed={}),
        ],
        final=(
            "The study found 4 viewing events. Daily area counts a cell once when "
            "an event occurs at its center, and the field-of-regard envelope is not "
            "continuous viewing."
        ),
    )


def _registry_conical_trace(clock, failed_refresh=False):
    from eval.cases import CONICAL_SHARED_ID, CONICAL_SHARED_REVISION

    trace = _conical_trace(clock)
    selection = {
        "configuration_id": CONICAL_SHARED_ID,
        "revision": CONICAL_SHARED_REVISION,
        "source": "reviewed_shared",
        "selection": "explicit_exact_revision",
        "source_change_checks": [],
        "source_change_notices": [],
        "refresh_uncertainty": [],
    }
    prepare = trace.calls[0]
    prepare.args = {
        "grid_rows": 2,
        "grid_columns": 2,
        "configuration_id": CONICAL_SHARED_ID,
        "configuration_revision": CONICAL_SHARED_REVISION,
    }
    prepare.parsed["registry_selection"] = selection
    finish = trace.calls[1]
    finish.parsed["configuration_id"] = CONICAL_SHARED_ID
    finish.parsed["configuration_revision"] = CONICAL_SHARED_REVISION
    finish.parsed["registry_selection"] = selection

    calls = []
    if failed_refresh:
        failed_check = {
            "status": "failed",
            "failure_reason": "The source page was unavailable during this check.",
        }
        selection["refresh_uncertainty"] = [failed_check]
        calls.append(
            Call(
                "record_instrument_configuration_refresh",
                {"status": "failed"},
                False,
                "{}",
                parsed={"status": "failed", "source_change_check": failed_check},
            )
        )
    calls.append(
        Call(
            "lookup_instrument_configurations",
            {"instrument": "AMSR2", "spacecraft": "GCOM-W1"},
            False,
            "{}",
            parsed={
                "selection_required": True,
                "selection": None,
                "candidates": [
                    {
                        "configuration_id": CONICAL_SHARED_ID,
                        "revision": CONICAL_SHARED_REVISION,
                        "registry_status": "reviewed_shared",
                        "refresh_uncertainty": selection["refresh_uncertainty"],
                    }
                ],
            },
        )
    )
    calls.extend(trace.calls)
    trace.calls = calls
    if failed_refresh:
        trace.final += " The failed refresh leaves current source status unknown."
    return trace


def test_conical_eval_requires_prepared_accepted_revision_and_event_semantics():
    clock = _clock()
    case = next(
        case for case in build_cases(clock) if case.id == "conical_amsr2_complete"
    )
    trace = _conical_trace(clock)
    checks = case.checks(trace, clock)
    assert all(checks.values()), checks

    trace.calls[1].args["accepted_setup_revision"] = "sha256:old"
    checks = case.checks(trace, clock)
    assert not checks["accepted_revision"]
    assert not all(checks.values())

    trace = _conical_trace(clock)
    trace.calls.pop(0)
    checks = case.checks(trace, clock)
    assert not checks["prepared"]
    assert not all(checks.values())


def test_conical_eval_rejects_continuous_viewing_and_whole_cell_claims():
    clock = _clock()
    case = next(case for case in build_cases(clock) if case.id == "conical_amsr2_complete")
    for final in (
        "There were 4 events. Every point in each cell was continuously observed "
        "throughout each envelope.",
        "There were 4 viewing events at cell centers. The envelopes mean "
        "continuous viewing of the whole cell.",
        "The study found 4 events. Daily area is cell-center sampled; the "
        "envelope is not continuous viewing. Every point in each cell was observed.",
        "There were 4 events at cell centers. The envelope is not continuous "
        "viewing, but every point in every cell was observed.",
    ):
        trace = _conical_trace(clock)
        trace.final = final
        assert not case.checks(trace, clock)["reported"], final


def test_conical_reporting_accepts_negated_continuous_claims():
    clock = _clock()
    case = next(
        case for case in build_cases(clock) if case.id == "conical_amsr2_complete"
    )
    for final in (
        "There were 4 events at cell centers. The envelope is not continuous "
        "viewing. A gap area of zero wouldn't mean continuous observation.",
        "There were 4 events at cell centers. The envelope is not continuous "
        "viewing. Continuous viewing and uncovered durations aren't available.",
        "There were 4 events at cell centers. The envelope is not continuous "
        "viewing. Each whole cell is classified observed or gap based on the "
        "event at its center.",
    ):
        trace = _conical_trace(clock)
        trace.final = final
        assert case.checks(trace, clock)["reported"], final


def test_eval_geometry_check_handles_missing_converted_swath_value():
    clock = _clock()
    case = next(
        case
        for case in build_cases(clock)
        if case.id == "resolver_lookup_supported"
    )
    start = clock.d0
    end = start.replace(day=start.day + 1)
    summary = {
        "result_id": "result-1",
        "window_start_utc": start.isoformat(),
        "window_end_utc_exclusive": end.isoformat(),
        "roi_bbox": [-113.0, 32.0, -111.0, 35.0],
        "geometry_model": {
            "geometry_input_kind": "field_of_regard_deg",
            "geometry_input_value": 110.0,
        },
        "geometry_assessment": {
            "status": "supported",
            "source_text": "A 110 degree field of regard.",
        },
    }
    trace = Trace(
        prompt=case.prompt,
        calls=[Call("finish_access_run", {}, False, "{}", parsed=summary)],
        final="The study completed.",
    )
    checks = case.checks(trace, clock)
    assert checks["completed"]
    assert checks["geometry"] is False


def test_registry_eval_requires_lookup_and_explicit_revision_selection():
    clock = _clock()
    case = next(
        case for case in build_cases(clock) if case.id == "conical_amsr2_registry_selection"
    )
    trace = _registry_conical_trace(clock)
    checks = case.checks(trace, clock)
    assert all(checks.values()), checks

    trace.calls.pop(0)
    checks = case.checks(trace, clock)
    assert not checks["looked_up_candidate_without_selection"]
    assert not all(checks.values())

    trace = _registry_conical_trace(clock)
    trace.calls[1].args["configuration_revision"] = "latest"
    checks = case.checks(trace, clock)
    assert not checks["prepared_exact_registry_revision"]
    assert not all(checks.values())

    trace = _registry_conical_trace(clock)
    trace.calls[1].args["configuration"] = None
    checks = case.checks(trace, clock)
    assert checks["prepared_exact_registry_revision"]


def test_failed_refresh_eval_requires_uncertainty_in_the_saved_result_and_reply():
    clock = _clock()
    case = next(
        case for case in build_cases(clock) if case.id == "conical_amsr2_failed_refresh"
    )
    trace = _registry_conical_trace(clock, failed_refresh=True)
    checks = case.checks(trace, clock)
    assert all(checks.values()), checks

    refresh = trace.calls.pop(0)
    lookup = trace.calls.pop(0)
    trace.calls[0:0] = [lookup, refresh]
    checks = case.checks(trace, clock)
    assert all(checks.values()), checks

    trace = _registry_conical_trace(clock, failed_refresh=True)
    refresh = trace.calls.pop(0)
    lookup = trace.calls.pop(0)
    prepare = trace.calls.pop(0)
    trace.calls[0:0] = [lookup, prepare, refresh]
    checks = case.checks(trace, clock)
    assert not checks["recorded_failed_refresh_before_prepare"]

    trace = _registry_conical_trace(clock, failed_refresh=True)
    trace.final = (
        "The failed refresh leaves source status unknown; the source cannot be "
        "considered unchanged."
    )
    checks = case.checks(trace, clock)
    assert checks["reported_source_uncertainty"]

    trace.final = "Source status is unknown, but the sources are unchanged."
    checks = case.checks(trace, clock)
    assert not checks["reported_source_uncertainty"]

    trace.final = "Source status is unknown, but the source page has not changed."
    checks = case.checks(trace, clock)
    assert not checks["reported_source_uncertainty"]

    trace.final = (
        "Source status is unknown; there is no claim that the sources are unchanged."
    )
    checks = case.checks(trace, clock)
    assert checks["reported_source_uncertainty"]

    trace.final = (
        "Source status is unknown; it is not a claim that the sources and "
        "geometry are unchanged."
    )
    checks = case.checks(trace, clock)
    assert checks["reported_source_uncertainty"]

    trace.final = "Source status is unknown; nothing was verified as unchanged."
    checks = case.checks(trace, clock)
    assert checks["reported_source_uncertainty"]

    trace.final = (
        "Source status is unknown; the failed check does not claim that the "
        "sources remain unchanged."
    )
    checks = case.checks(trace, clock)
    assert checks["reported_source_uncertainty"]

    trace.final = (
        "Source status is unknown; the failed check does not confirm the sources "
        "are unchanged."
    )
    checks = case.checks(trace, clock)
    assert checks["reported_source_uncertainty"]

    trace.final = (
        "Source status is unknown; the source was not verified, and the sources "
        "are unchanged."
    )
    checks = case.checks(trace, clock)
    assert not checks["reported_source_uncertainty"]

    trace.final = "Source status is unknown, but the source was never changed."
    checks = case.checks(trace, clock)
    assert not checks["reported_source_uncertainty"]

    trace.final = (
        "Source status is unknown. No fresh evidence was available and the "
        "sources are unchanged."
    )
    checks = case.checks(trace, clock)
    assert not checks["reported_source_uncertainty"]

    trace.final = trace.final.replace("unknown", "unchanged")
    checks = case.checks(trace, clock)
    assert checks["recorded_failed_refresh_before_prepare"]
    assert checks["result_retains_refresh_uncertainty"]
    assert not checks["reported_source_uncertainty"]


def test_conical_unresolved_sector_requires_a_choice_before_running():
    clock = _clock()
    case = next(
        case for case in build_cases(clock) if case.id == "conical_unresolved_sector"
    )
    asked = Trace(
        prompt=case.prompt,
        final=(
            "I need your choice between the nominal performance sector and the "
            "wider product sector before I can run this. Which should I use?"
        ),
    )
    checks = case.checks(asked, clock)
    assert all(checks.values()), checks

    silent = Trace(
        prompt=case.prompt,
        final="I chose the product sector, so the study is ready.",
    )
    checks = case.checks(silent, clock)
    assert checks["did_not_finish"]
    assert not checks["asked_for_choice"]
    assert not checks["no_silent_choice"]
    assert not all(checks.values())

    invented = Trace(
        prompt=case.prompt,
        calls=[
            Call(
                "prepare_conical_study",
                {"grid_rows": 2, "grid_columns": 2},
                False,
                "{}",
                parsed={"setup_revision": "sha256:invented"},
            )
        ],
        final="I used the product sector and prepared it.",
    )
    checks = case.checks(invented, clock)
    assert not checks["no_invented_choice"]
    assert not all(checks.values())

    legacy = Trace(
        prompt=case.prompt,
        calls=[
            Call(
                "finish_access_run",
                {},
                False,
                "{}",
                parsed={"result_id": "legacy-1"},
            )
        ],
        final="Which sector should I use?",
    )
    checks = case.checks(legacy, clock)
    assert not checks["did_not_finish"]
    assert not all(checks.values())

def test_conical_eval_rejects_uncovered_or_unsupported_setup():
    clock = _clock()
    for case_id, final in (
        (
            "conical_applicability_rejected",
            "The requested window is outside the established applicability interval; "
            "that date range is not covered, so I did not run it.",
        ),
        (
            "conical_missing_evidence",
            "The measured cone and sector evidence is missing, so I cannot run this "
            "configuration. Please provide the source evidence.",
        ),
    ):
        case = next(case for case in build_cases(clock) if case.id == case_id)
        checks = case.checks(Trace(prompt=case.prompt, final=final), clock)
        assert all(checks.values()), (case_id, checks)


def test_conical_rejection_eval_rejects_legacy_calculations():
    clock = _clock()
    for case_id in ("conical_applicability_rejected", "conical_missing_evidence"):
        case = next(case for case in build_cases(clock) if case.id == case_id)
        trace = Trace(
            prompt=case.prompt,
            calls=[Call("finish_access_run", {}, False, "{}", {"result_id": "legacy-1"})],
            final="The applicability interval and source evidence are insufficient.",
        )
        assert not case.checks(trace, clock)["did_not_finish"]


def test_pointed_eval_region_check_uses_latitude_longitude_order():
    clock = Clock(datetime(2026, 10, 9, 18, 56, 47, tzinfo=timezone.utc))
    case = next(
        case for case in build_cases(clock) if case.id == "pointed_s1c_iw_complete"
    )
    summary = {
        "result_id": "result-1",
        "window_start_utc": "2026-10-10T00:00:00Z",
        "window_end_utc_exclusive": "2026-10-10T02:00:00Z",
        "roi_bbox": [80.435, 17.482, 80.440, 17.488],
    }
    trace = Trace(
        prompt=case.prompt,
        calls=[Call("finish_pointed_study", {}, False, "{}", parsed=summary)],
    )

    assert case.checks(trace, clock)["window_and_region"]


def test_pointed_eval_requires_choices_before_any_study_setup():
    clock = _clock()
    case = next(
        case
        for case in build_cases(clock)
        if case.id == "pointed_registry_choices_before_acceptance"
    )
    configuration_id = "sentinel-1c-c-sar-iw-fixed-view"
    candidate = {
        "configuration_id": configuration_id,
        "revision": "2026-10-30.1",
        "spacecraft": {"name": "Sentinel-1C", "norad_id": 62261},
        "operating_mode": "IW",
        "approximation_status": "limited",
    }
    trace = Trace(
        prompt=case.prompt,
        calls=[
            Call(
                "lookup_instrument_configurations",
                {"instrument": "C-SAR", "spacecraft": "Sentinel-1"},
                False,
                "{}",
                parsed={
                    "candidate_count": 1,
                    "selection_required": True,
                    "selection": None,
                    "candidates": [candidate],
                },
            )
        ],
        final=(
            "Exactly one supported option is in the registry: Sentinel-1C C-SAR in IW: "
            f"{configuration_id}, revision 2026-10-30.1. It is limited: a fixed "
            "roll approximates steering, one rectangular view represents the IW "
            "subswaths, and the along-track view uses the antenna beam width. "
            "Would you like me to proceed with this exact spacecraft and mode "
            "and accept these approximations?"
        ),
    )
    checks = case.checks(trace, clock)
    assert all(checks.values()), checks

    trace.calls.insert(
        0,
        Call(
            "lookup_instrument_configurations",
            {"instrument": "C-SAR"},
            False,
            "{}",
            parsed={
                "candidate_count": 0,
                "selection_required": False,
                "selection": None,
                "candidates": [],
            },
        ),
    )
    checks = case.checks(trace, clock)
    assert all(checks.values()), checks

    prematurely_prepared = Trace(
        prompt=case.prompt,
        calls=[
            *trace.calls,
            Call(
                "prepare_pointed_study",
                {"configuration_id": configuration_id},
                False,
                "{}",
                parsed={"configuration_id": configuration_id},
            ),
        ],
        final=trace.final,
    )
    checks = case.checks(prematurely_prepared, clock)
    assert checks["looked_up_supported_options"]
    assert checks["identified_spacecraft_and_mode"]
    assert checks["presented_modeling_choices"]
    assert not checks["asked_before_study_setup"]
