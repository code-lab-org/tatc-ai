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


def _conical_trace(clock):
    summary = {
        "result_id": "conical-1",
        "result_kind": "conical_study",
        "accounting_method": "conical_events_v1",
        "setup_revision": "sha256:abc",
        "configuration_id": "amsr2-gcom-w1-conical-product-sector-eval",
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
