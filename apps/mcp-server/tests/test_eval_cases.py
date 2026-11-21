"""Offline checks for geometry behavior scored by the tool-calling eval."""

import copy
import json
from datetime import datetime, timedelta, timezone
from unittest import mock

from src import source_library
from src.conical_config import ConicalConfiguration

from eval import cases as eval_cases
from eval.cases import (
    CONICAL_CLAIMS_CONFIG,
    CONICAL_CONFIG,
    SOURCE_LIBRARY_FIXTURE,
    Call,
    Clock,
    Trace,
    _mixed_complete,
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


def test_eval_cases_cover_combined_mixed_and_shared_spacecraft_daylight_workflows():
    cases = build_cases(_clock())
    mixed = next(case for case in cases if case.id == "mixed_amsr2_s1c_complete")
    assert "prepare_mixed_study" in mixed.prompt
    assert "distinct-epoch spacing" in mixed.prompt.lower()
    assert "each exact configuration id" in mixed.prompt.lower()
    assert "get_mixed_events limit=5" in mixed.prompt
    assert "get_mixed_cells limit=2" in mixed.prompt
    assert "get_mixed_event_spacing limit=5" in mixed.prompt
    assert "Copy the exact ISO timestamps unchanged" in mixed.prompt
    assert "all five views for the combined union" in mixed.prompt.lower()
    assert "all five views for the amsr2 configuration" in mixed.prompt.lower()
    assert "get_mixed_events for the sentinel-1c configuration" in mixed.prompt.lower()

    daylight = next(
        case for case in cases if case.id == "mixed_shared_spacecraft_daylight"
    )
    assert "JSON array of two objects" in daylight.prompt
    assert '"configuration": <this JSON object>' in daylight.prompt
    assert "not a quoted JSON string" in daylight.prompt
    assert "get_mixed_events limit=5" in daylight.prompt
    assert "get_mixed_cells limit=2" not in daylight.prompt
    assert "get_mixed_event_spacing limit=5" not in daylight.prompt
    assert "Copy the exact ISO timestamps unchanged" in daylight.prompt
    assert "combined events and daily union" in daylight.prompt.lower()
    assert "get_mixed_events for each exact configuration id" in daylight.prompt.lower()
    marker = "Configuration JSON: "
    config = json.loads(daylight.prompt.split(marker, 1)[1])
    assert config["configuration_id"] == "amsr2-gcom-w1-daylight-study-assumption-eval"
    assert config["spacecraft"]["norad_id"] == 38337
    assert config["geometry"]["illumination_requirement"] == "target_sunlit"
    assert config["geometry"]["illumination_requirement_basis"] == "study_assumption"
    assert "sensitivity" in config["geometry"]["illumination_requirement_note"].lower()
    assert "does not establish" in config["geometry"]["illumination_requirement_note"].lower()


def test_mixed_eval_checker_requires_exact_acceptance_and_each_fetch_scope():
    expected = {
        "amsr2-gcom-w1-conical-product-sector": "2026-10-27.1",
        "sentinel-1c-c-sar-iw-fixed-view": "2026-10-30.1",
    }
    setup_revision = "sha256:mixed-eval"
    snapshots = [
        {
            "configuration_id": configuration_id,
            "configuration_revision": revision,
            "configuration": {"evidence": [{}]},
            "satellite": {"norad_id": index},
        }
        for index, (configuration_id, revision) in enumerate(expected.items(), start=1)
    ]
    summary = {
        "result_id": "mixed-result",
        "result_kind": "mixed_study",
        "accounting_method": "mixed_events_union_v1",
        "completeness_status": "complete",
        "setup_revision": setup_revision,
        "summary_counts": {"event_count": 3},
        "modeled_configurations": [
            {
                "configuration_id": configuration_id,
                "configuration_revision": revision,
            }
            for configuration_id, revision in expected.items()
        ],
        "provenance": {
            "setup_revision": setup_revision,
            "setup_snapshot": {"configurations": snapshots},
        },
    }
    calls = [
        Call(
            "lookup_instrument_configurations",
            {},
            False,
            "{}",
            parsed={
                "selection": None,
                "candidates": [
                    {"configuration_id": configuration_id, "revision": revision}
                ],
            },
        )
        for configuration_id, revision in expected.items()
    ]
    calls.extend(
        [
            Call(
                "prepare_mixed_study",
                {
                    "configurations": [
                        {
                            "configuration_id": configuration_id,
                            "configuration_revision": revision,
                        }
                        for configuration_id, revision in expected.items()
                    ]
                },
                False,
                "{}",
                parsed={"status": "awaiting_acceptance"},
            ),
            Call(
                "finish_mixed_study",
                {
                    "accepted_setup_revision": setup_revision,
                    "accept_setup": True,
                },
                False,
                "{}",
                parsed=summary,
            ),
        ]
    )
    for configuration_id in [None, *expected]:
        for name in {
            "get_mixed_events",
            "get_mixed_daily",
            "get_mixed_cells",
            "get_mixed_revisit",
            "get_mixed_event_spacing",
        }:
            args = {"result_id": "mixed-result"}
            if name in {"get_mixed_events", "get_mixed_event_spacing"}:
                args["limit"] = 5
            elif name == "get_mixed_cells":
                args["limit"] = 2
            if configuration_id is not None:
                args["configuration_id"] = configuration_id
            calls.append(
                Call(
                    name,
                    args,
                    False,
                    "{}",
                    parsed={
                        "scope": (
                            "combined"
                            if configuration_id is None
                            else "configuration"
                        ),
                        "provenance": {"setup_revision": setup_revision},
                        "definitions": {},
                        "warnings": [],
                        "limit": {
                            "get_mixed_events": 5,
                            "get_mixed_cells": 2,
                            "get_mixed_event_spacing": 5,
                        }.get(name),
                    },
                )
            )
    trace = Trace(
        prompt="mixed study",
        calls=calls,
        final=(
            "There are 3 modeled events, not confirmed acquisitions. Cell-center "
            "samples and envelope periods do not show continuous viewing."
        ),
    )
    case = next(
        case
        for case in build_cases(_clock())
        if case.id == "mixed_amsr2_s1c_complete"
    )
    checks = case.checks(trace, _clock())
    assert all(checks.values()), checks

    original_event_count = summary["summary_counts"]["event_count"]
    summary["summary_counts"]["event_count"] = 4
    trace.final = (
        "4 modeled acquisitions confirmed. Every cell was continuously viewed "
        "throughout each envelope."
    )
    checks = case.checks(trace, _clock())
    assert all(
        value for name, value in checks.items() if name != "reported_model_limits"
    ), checks
    assert not checks["reported_model_limits"]

    summary["summary_counts"]["event_count"] = original_event_count
    for final in (
        "There were 3 modeled acquisitions confirmed. Cell-center samples and "
        "envelope periods do not show continuous viewing.",
        "There were 3 modeled events. Every point in every cell was viewed. "
        "Cell-center samples and envelope periods are not continuous viewing time.",
        "There were 3 modeled events, not confirmed acquisitions. "
        "The whole-cell area was viewed. "
        "Cell-center samples and envelope periods are not continuous viewing time.",
        "There were 3 modeled events at cell centers. Viewing is continuous "
        "throughout each envelope.",
    ):
        trace.final = final
        checks = case.checks(trace, _clock())
        assert all(
            value for name, value in checks.items() if name != "reported_model_limits"
        ), checks
        assert not checks["reported_model_limits"], final

    for final in (
        "There are 3 modeled events, not confirmed acquisitions. Cell-center "
        "samples and envelope periods do not show continuous viewing.",
        "There are 3 modelled opportunities; no acquisition was confirmed. "
        "Cell-centre samples are not whole-cell coverage, and envelope periods "
        "aren't continuous viewing time.",
        "There are 3 modeled opportunities, not confirmed acquisitions. "
        "Cell-center samples are not whole-cell coverage, and envelope periods "
        "are metadata only. Time covered and revisit are unavailable, and "
        "continuous viewing is not reported.",
        "There are 3 modeled opportunities, not confirmed acquisitions. One "
        "continuous rectangular view is a geometry approximation. Cell-center "
        "samples and envelopes do not establish continuous viewing time or "
        "full-cell coverage.",
    ):
        trace.final = final
        assert case.checks(trace, _clock())["reported_model_limits"], final

    trace.final = (
        "There are 3 modeled events, not confirmed acquisitions. Cell-center "
        "samples and envelope periods do not show continuous viewing."
    )

    configuration_ids = list(expected)
    fetch_requirements = {
        None: {
            "get_mixed_events",
            "get_mixed_daily",
            "get_mixed_cells",
            "get_mixed_revisit",
            "get_mixed_event_spacing",
        },
        configuration_ids[0]: {
            "get_mixed_events",
            "get_mixed_daily",
            "get_mixed_cells",
            "get_mixed_revisit",
            "get_mixed_event_spacing",
        },
        configuration_ids[1]: {"get_mixed_events"},
    }
    partial_trace = Trace(
        prompt=trace.prompt,
        calls=[
            call
            for call in trace.calls
            if call.name not in {
                "get_mixed_events",
                "get_mixed_daily",
                "get_mixed_cells",
                "get_mixed_revisit",
                "get_mixed_event_spacing",
            }
            or call.name
            in fetch_requirements.get(call.args.get("configuration_id"), set())
        ],
        final=trace.final,
        turns=trace.turns,
        stop=trace.stop,
    )
    partial_checks = _mixed_complete(
        expected,
        tuple(expected),
        fetch_requirements=fetch_requirements,
    )(partial_trace, _clock())
    assert all(partial_checks.values()), partial_checks

    trace.final = trace.final.replace("modeled", "modelled")
    assert case.checks(trace, _clock())["reported_model_limits"]
    trace.final = trace.final.replace("modelled", "modeled")

    for call in trace.calls:
        if call.name in {"get_mixed_events", "get_mixed_cells", "get_mixed_event_spacing"}:
            call.args.pop("limit", None)
    checks = case.checks(trace, _clock())
    assert checks["fetch_page_sizes"]

    next(call for call in trace.calls if call.name == "get_mixed_events").parsed[
        "limit"
    ] = 21
    checks = case.checks(trace, _clock())
    assert not checks["fetch_page_sizes"]

    trace.calls = [
        call
        for call in trace.calls
        if not (call.name == "get_mixed_daily" and call.args.get("configuration_id") is None)
    ]
    checks = case.checks(trace, _clock())
    assert not checks["combined_and_configuration_fetches"]


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
        "There were 4 events at cell centers. The envelope is not continuous "
        "viewing time, but the cells were continuously viewed throughout every envelope.",
        "There were 4 events at cell centers. The envelope is not continuous "
        "viewing time. Every grid cell was viewed.",
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
        "There were 4 events at cell centers. Envelope viewing is not continuous.",
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


def test_partial_mixed_eval_checker_requires_omission_scope_and_unknown_access():
    configuration_id = "amsr2-gcom-w1-conical-product-sector"
    revision = "2026-10-27.1"
    omission_id = "omission:partial-eval"
    setup_revision = "sha256:mixed-partial-eval"
    summary = {
        "result_id": "mixed-partial-result",
        "result_kind": "mixed_study",
        "accounting_method": "mixed_events_union_v1",
        "completeness_status": "partial",
        "setup_revision": setup_revision,
        "summary_counts": {"event_count": 2},
        "modeled_configurations": [
            {
                "configuration_id": configuration_id,
                "configuration_revision": revision,
                "access_status": "modeled",
            }
        ],
        "omitted_configurations": [
            {
                "omission_id": omission_id,
                "instrument": "SeaWiFS",
                "spacecraft": "OrbView-2",
                "access_status": "unknown",
                "reason_code": "unsupported_instrument",
            }
        ],
        "provenance": {
            "setup_revision": setup_revision,
            "setup_snapshot": {
                "configurations": [
                    {
                        "configuration_id": configuration_id,
                        "configuration_revision": revision,
                        "configuration": {"evidence": [{}]},
                    }
                ]
            },
        },
    }
    omission_args = [
        {
            "instrument": "SeaWiFS",
            "spacecraft": "OrbView-2",
            "status": "omitted",
            "reason_code": "unsupported_instrument",
            "reason": "No reviewed configuration exists.",
        }
    ]
    calls = [
        Call(
            "prepare_mixed_study",
            {
                "configurations": [
                    {
                        "configuration_id": configuration_id,
                        "configuration_revision": revision,
                    }
                ],
                "omissions": omission_args,
            },
            False,
            "{}",
            parsed={"status": "awaiting_acceptance"},
        ),
        Call(
            "finish_mixed_study",
            {
                "accepted_setup_revision": setup_revision,
                "accept_setup": True,
                "accepted_omissions": [omission_id],
            },
            False,
            "{}",
            parsed=summary,
        ),
    ]
    header = {
        "completeness_status": "partial",
        "omitted_configurations": summary["omitted_configurations"],
        "provenance": {"setup_revision": setup_revision},
        "definitions": {"omitted_access": "unknown access is not zero"},
    }
    for name, scope in (
        ("get_mixed_events", None),
        ("get_mixed_daily", None),
        ("get_mixed_cells", None),
        ("get_mixed_events", configuration_id),
    ):
        arguments = {"result_id": "mixed-partial-result"}
        if scope is not None:
            arguments["configuration_id"] = scope
        calls.append(Call(name, arguments, False, "{}", parsed=dict(header)))
    final = (
        "This partial study reports 2 modeled AMSR2 events. SeaWiFS was omitted "
        "because no reviewed configuration exists, so its access is unknown. The "
        "totals describe only the modeled AMSR2 subset and are modeled "
        "opportunities, not confirmed acquisitions. Cell-center samples are not "
        "whole-cell coverage, and envelopes are not continuous viewing time."
    )
    trace = Trace(prompt="partial mixed study", calls=calls, final=final)
    case = next(
        case
        for case in build_cases(_clock())
        if case.id == "mixed_partial_amsr2_unsupported"
    )
    checks = case.checks(trace, _clock())
    assert all(checks.values()), checks

    trace.calls[1].args.pop("accepted_omissions")
    checks = case.checks(trace, _clock())
    assert checks["completed"]
    assert not checks["accepted_exact_omission_scope"]

    trace.calls[1].args["accepted_omissions"] = [omission_id]
    trace.calls[1].parsed["completeness_status"] = "complete"
    checks = case.checks(trace, _clock())
    assert not checks["completed"]

    trace.calls[1].parsed["completeness_status"] = "partial"
    trace.final = "SeaWiFS had zero access; the study is complete."
    checks = case.checks(trace, _clock())
    assert not checks["reported_partial_limits"]


def test_partial_staged_case_requires_both_spacecraft():
    clock = _clock()
    case = next(
        case
        for case in build_cases(clock)
        if case.id == "mixed_partial_s1c_amsr2_omitted"
    )
    trace = Trace(
        prompt="partial staged omission",
        calls=[
            Call(
                "begin_access_run",
                {},
                False,
                "{}",
                parsed={"satellites": [{"norad_id": 62261}]},
            )
        ],
        final="",
    )
    assert not case.checks(trace, clock)["staged_expected_spacecraft"]

    trace.calls[0].parsed["satellites"].append({"norad_id": 38337})
    assert case.checks(trace, clock)["staged_expected_spacecraft"]

    abandoned_begin = Call(
        "begin_access_run",
        {},
        False,
        "{}",
        parsed={"satellites": [{"norad_id": 62261}, {"norad_id": 38337}]},
    )
    finish_s1c_only = Call(
        "finish_mixed_study",
        {},
        False,
        "{}",
        parsed={
            "result_id": "mixed-staged-only-s1c",
            "satellites": [{"norad_id": 62261}],
        },
    )
    trace = Trace(
        prompt="partial staged omission",
        calls=[finish_s1c_only, abandoned_begin],
        final="",
    )
    assert not case.checks(trace, clock)["staged_expected_spacecraft"]

    finish_both = Call(
        "finish_mixed_study",
        {},
        False,
        "{}",
        parsed={
            "result_id": "mixed-staged-both",
            "satellites": [{"norad_id": 62261}, {"norad_id": 38337}],
        },
    )
    trace = Trace(
        prompt="partial staged omission",
        calls=[finish_both, abandoned_begin],
        final="",
    )
    assert case.checks(trace, clock)["staged_expected_spacecraft"]


def test_mixed_follow_up_grid_checker_requires_two_runs_and_a_new_revision():
    case = next(
        item for item in build_cases(_clock()) if item.id == "mixed_follow_up_grid"
    )
    trace = Trace(prompt=case.prompt)
    trace.calls = [
        Call(
            "prepare_mixed_study",
            {"grid_rows": 2, "grid_columns": 2},
            False,
            "",
            {"setup_revision": "sha256:first"},
        ),
        Call(
            "finish_mixed_study",
            {"accept_setup": True},
            False,
            "",
            {"result_id": "result-one", "setup_revision": "sha256:first"},
        ),
        Call(
            "prepare_mixed_study",
            {"grid_rows": 3, "grid_columns": 3},
            False,
            "",
            {"setup_revision": "sha256:second"},
        ),
        Call(
            "finish_mixed_study",
            {"accept_setup": True},
            False,
            "",
            {"result_id": "result-two", "setup_revision": "sha256:second"},
        ),
    ]
    checks = case.checks(trace, _clock())
    assert all(checks.values()), checks

    trace.calls[3].parsed["setup_revision"] = "sha256:first"
    trace.calls[3].parsed["result_id"] = "result-one"
    checks = case.checks(trace, _clock())
    assert not checks["new_revision"]
    assert not checks["two_results"]

    trace.calls[2].args["configurations"] = [
        {"configuration_id": "different-configuration"}
    ]
    checks = case.checks(trace, _clock())
    assert not checks["same_scope"]


def _compact_evidence(records):
    """Project resolved evidence to the fields a study proposal returns."""
    keys = (
        "evidence_id",
        "authority",
        "title",
        "url",
        "document_revision",
        "locator",
        "supports",
    )
    return [{key: record[key] for key in keys} for record in records]


def _claims_prepare_trace(clock, sent_configuration, proposal_configuration, final):
    proposal = {
        "configuration_id": CONICAL_CLAIMS_CONFIG["configuration_id"],
        "configuration": proposal_configuration,
    }
    return Trace(
        prompt="Run the conical AMSR2 study with the claim-form configuration.",
        calls=[
            Call(
                "prepare_conical_study",
                {
                    "draft_id": "draft-1",
                    "grid_rows": 2,
                    "grid_columns": 2,
                    "configuration": sent_configuration,
                },
                False,
                "{}",
                parsed=proposal,
            )
        ],
        final=final,
    )


def test_claims_case_config_resolves_through_the_fixture_library(monkeypatch):
    """The claim-form case cites the one-document fixture library.

    Resolving its single claim yields one evidence record whose locator,
    authority, title, url, and document revision come from the fixture
    document, so the case exercises the claim form and the library
    resolution, not the registry configuration's inline evidence.
    """
    monkeypatch.setattr(source_library, "SOURCES_DIR", SOURCE_LIBRARY_FIXTURE)

    assert CONICAL_CLAIMS_CONFIG["schema_version"] == "conical-configuration-v2"
    assert "evidence" not in CONICAL_CLAIMS_CONFIG
    assert len(CONICAL_CLAIMS_CONFIG["source_claims"]) == 1
    resolved = ConicalConfiguration.model_validate(
        copy.deepcopy(CONICAL_CLAIMS_CONFIG)
    )

    assert resolved.schema_version == "conical-configuration-v1"
    assert "source_claims" not in resolved.model_dump(mode="json")
    records = resolved.model_dump(mode="json")["evidence"]
    assert len(records) == 1
    document = json.loads(
        (SOURCE_LIBRARY_FIXTURE / "eval-amsr2-claims-source.json").read_text()
    )
    excerpt = document["excerpts"][0]
    assert records[0]["evidence_id"] == "eval-source"
    assert records[0]["authority"] == document["authority"]
    assert records[0]["locator"] == excerpt["locator"]
    assert records[0]["excerpt"] == excerpt["text"]


def test_claims_case_requires_the_claim_form_and_resolved_evidence(monkeypatch):
    clock = _clock()
    monkeypatch.setattr(source_library, "SOURCES_DIR", SOURCE_LIBRARY_FIXTURE)
    case = next(
        case for case in build_cases(clock) if case.id == "conical_amsr2_claims"
    )
    resolved = ConicalConfiguration.model_validate(
        copy.deepcopy(CONICAL_CLAIMS_CONFIG)
    )
    proposal_configuration = {
        "schema_version": "conical-configuration-v1",
        "evidence": _compact_evidence(resolved.model_dump(mode="json")["evidence"]),
    }
    assert proposal_configuration["evidence"] == eval_cases._claims_expected_evidence()

    def _trace(sent_configuration, proposal, final):
        return _claims_prepare_trace(clock, sent_configuration, proposal, final)

    ready = (
        f"The {CONICAL_CLAIMS_CONFIG['configuration_id']} proposal is ready "
        "and awaiting acceptance."
    )
    trace = _trace(copy.deepcopy(CONICAL_CLAIMS_CONFIG), proposal_configuration, ready)
    checks = case.checks(trace, clock)
    assert all(checks.values()), checks

    # The sent arguments must validate against the advertised tool
    # schema. When the advertised surface required inline evidence
    # (before b9e1437), the claim form the prompt mandates was invalid
    # and the case failed even though the runtime accepted it; the
    # checkout's own schema decides.
    before_schema = copy.deepcopy(
        eval_cases._advertised_input_schema("prepare_conical_study")
    )
    configuration_schema = before_schema["properties"]["configuration"]["anyOf"][0]
    configuration_schema["required"] = configuration_schema["required"] + ["evidence"]
    with mock.patch.object(
        eval_cases, "_advertised_input_schema", return_value=before_schema
    ):
        checks = case.checks(trace, clock)
    assert not checks["advertised_schema_ok"]
    assert not all(checks.values())

    # A sent argument the advertised schema rejects, such as grid_rows
    # as a string, fails the schema check.
    changed_args = copy.deepcopy(CONICAL_CLAIMS_CONFIG)
    trace = _trace(changed_args, proposal_configuration, ready)
    trace.calls[0].args["grid_rows"] = "2"
    checks = case.checks(trace, clock)
    assert not checks["advertised_schema_ok"]
    assert not all(checks.values())

    # A model that rounds or changes a geometry value fails the
    # unchanged-configuration check even though the schema accepts it.
    changed = copy.deepcopy(CONICAL_CLAIMS_CONFIG)
    changed["geometry"]["along_track_field_of_view"]["value"] = 1.4
    trace = _trace(changed, proposal_configuration, ready)
    checks = case.checks(trace, clock)
    assert checks["advertised_schema_ok"]
    assert not checks["configuration_unchanged"]
    assert not all(checks.values())

    # A model that converted the claims to inline evidence, the form the
    # advertised schema required before the advertised-schema fix, fails
    # the unchanged-configuration check even though the server accepts
    # the result.
    inline = copy.deepcopy(CONICAL_CLAIMS_CONFIG)
    inline.pop("source_claims")
    inline["schema_version"] = "conical-configuration-v1"
    inline["evidence"] = resolved.model_dump(mode="json")["evidence"]
    trace = _trace(inline, proposal_configuration, "The proposal is ready.")
    checks = case.checks(trace, clock)
    assert not checks["configuration_unchanged"]
    assert not all(checks.values())

    # A proposal that echoes the claims instead of resolving them through
    # the library fails the resolution check.
    unresolved = {
        "schema_version": "conical-configuration-v1",
        "evidence": CONICAL_CLAIMS_CONFIG["source_claims"],
    }
    trace = _trace(
        copy.deepcopy(CONICAL_CLAIMS_CONFIG),
        unresolved,
        "The proposal is ready.",
    )
    checks = case.checks(trace, clock)
    assert not checks["claims_resolved"]
    assert not all(checks.values())

    # A prepare that never happened, or a reply that drops the
    # configuration identity, fails too.
    trace = _trace(
        copy.deepcopy(CONICAL_CLAIMS_CONFIG),
        proposal_configuration,
        "The proposal is ready.",
    )
    trace.calls[0].error = True
    trace.calls[0].parsed = None
    checks = case.checks(trace, clock)
    assert not checks["prepared"]
    assert not all(checks.values())
    trace = _trace(
        copy.deepcopy(CONICAL_CLAIMS_CONFIG),
        proposal_configuration,
        "The study proposal is ready and awaiting acceptance.",
    )
    checks = case.checks(trace, clock)
    assert not checks["reported"]
    assert not all(checks.values())


