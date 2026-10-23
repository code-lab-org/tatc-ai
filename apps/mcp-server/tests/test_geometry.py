"""Geometry resolver tests: parsing, status, validation, conversion."""

import pytest
from tatc.utils import field_of_regard_to_swath_width

from src.geometry import (
    assess_geometry_candidates,
    assess_manual_geometry,
    convert_swath_to_for,
    extract_geometry_candidates,
    resolve_geometry_status,
    validate_geometry_inputs,
)


def test_msi_style_swath_is_not_supported_without_positive_evidence():
    specs = {"Scanning Technique": "Pushbroom. Swath 290 km"}
    candidates = extract_geometry_candidates(specs)
    assert len(candidates) == 1
    [candidate] = candidates
    assert candidate["kind"] == "swath_width_km"
    assert candidate["value"] == 290.0
    assert candidate["confidence"] == "high"
    assert "usable_for_coverage" not in candidate
    status, _, recommended = resolve_geometry_status(candidates)
    assert status == "needs_clarification"
    assert recommended is None


def test_positive_evidence_supports_continuous_nadir_centered_geometry():
    specs = {
        "Scanning Technique": (
            "A continuous nadir-centered swath of 290 km has no gaps, and no "
            "separate pointing is needed to reach that width."
        )
    }
    candidates = extract_geometry_candidates(specs)
    status, reason, recommended = resolve_geometry_status(candidates, specs)
    assert status == "supported"
    assert recommended["value"] == 290.0
    assert "positive evidence" not in reason.lower()


def test_insufficient_manual_description_requires_clarification():
    with pytest.raises(ValueError, match="requires clarification"):
        assess_manual_geometry(
            None,
            290.0,
            "arbitrary citation",
            "Swath 290 km",
        )


def test_manual_value_must_match_the_source_candidate():
    description = (
        "Continuous nadir-centered swath 50 km, no gaps, and no separate "
        "pointing needed."
    )
    with pytest.raises(ValueError, match="does not match a candidate"):
        assess_manual_geometry(None, 50.04, "source", description)


@pytest.mark.parametrize(
    "description",
    [
        "Swath -290 km. Continuous nadir-centered swath with no gaps.",
        "Swath 25-290 km. Continuous nadir-centered swath with no gaps.",
        "Swath 25 to 290 km. Continuous nadir-centered swath with no gaps.",
    ],
)
def test_signed_or_ranged_swath_is_not_extracted_as_a_positive_scalar(description):
    candidates = extract_geometry_candidates({"Scanning Technique": description})
    assert not [candidate for candidate in candidates if candidate["kind"] == "swath_width_km"]


def test_modis_half_angle_is_derived_but_needs_clarification():
    specs = {"Scanning Technique": "Cross-track scan, fixed \xb155-degree viewing angle"}
    candidates = extract_geometry_candidates(specs)
    derived = [c for c in candidates if c["kind"] == "field_of_regard_deg"]
    assert derived and derived[0]["value"] == 110.0
    assert derived[0]["derived"] is True
    # A bare half angle must not silently become coverage geometry.
    assert all("usable_for_coverage" not in c for c in candidates)
    status, reason, recommended = resolve_geometry_status(candidates)
    assert status == "needs_clarification"
    assert recommended is None
    assert "does not establish" in reason


def test_steerable_swath_needs_clarification():
    specs = {
        "Scanning Technique": "Pushbroom; swath 25 km steerable within "
        "a field of regard of 300 km"
    }
    candidates = extract_geometry_candidates(specs)
    assert candidates
    assert all("usable_for_coverage" not in c for c in candidates)
    status, _, _ = resolve_geometry_status(candidates)
    assert status == "needs_clarification"


def test_limb_sounder_needs_clarification():
    specs = {"Scanning Technique": "Limb viewing with 63 km swath."}
    candidates = extract_geometry_candidates(specs)
    assert candidates and candidates[0]["kind"] == "swath_width_km"
    assert "needs_review" not in candidates[0]
    assert "usable_for_coverage" not in candidates[0]
    status, _, _ = resolve_geometry_status(candidates)
    assert status == "needs_clarification"


def test_conical_swath_needs_evidence_but_side_looking_is_approximate():
    conical = extract_geometry_candidates(
        {"Scanning Technique": "Conical: 55\xb0 zenith angle; swath: 1450 km"}
    )
    assert resolve_geometry_status(conical)[0] == "needs_clarification"
    side = extract_geometry_candidates(
        {"Scanning Technique": "Side-looking, 15-45\xb0 off-nadir, 250 km swath"}
    )
    assert side
    assert resolve_geometry_status(side)[0] == "approximate"


def test_fov_requires_clarification_before_use():
    specs = {"Swath": "FOV: 114 degree (cross track)."}
    candidates = extract_geometry_candidates(specs)
    assert candidates and candidates[0]["kind"] == "field_of_view_deg"
    assert "usable_for_coverage" not in candidates[0]
    status, _, _ = resolve_geometry_status(candidates)
    assert status == "needs_clarification"


def test_conflicting_swaths_require_clarification():
    specs = {
        "Scanning Technique": "Pushbroom. Swath 290 km",
        "Coverage / Cycle": "Wide mode swath 890 km",
    }
    candidates = extract_geometry_candidates(specs)
    widths = [c for c in candidates if c["kind"] == "swath_width_km"]
    assert len(widths) == 2
    status, reason, recommended = resolve_geometry_status(candidates)
    assert status == "needs_clarification"
    assert recommended is None
    assert "Conflicting values" in reason


def test_repeated_same_swath_with_positive_evidence_is_supported():
    specs = {
        "Scanning Technique": "Continuous nadir-centered swath 290 km, no gaps, no separate pointing needed.",
        "Coverage / Cycle": "Swath 290 km total",
    }
    status, _, recommended = resolve_geometry_status(
        extract_geometry_candidates(specs)
    )
    assert status == "supported"
    assert recommended["value"] == 290.0


def test_distinct_modes_require_a_choice_and_manual_mode_must_match():
    description = (
        "Narrow mode swath 25 km. Wide mode swath 100 km is a continuous "
        "nadir-centered swath with no gaps or separate pointing needed."
    )
    candidates = extract_geometry_candidates({"Modes": description})
    assert [(c["mode"], c["value"]) for c in candidates] == [
        ("narrow", 25.0),
        ("wide", 100.0),
    ]
    assert resolve_geometry_status(candidates)[0] == "needs_clarification"
    with pytest.raises(ValueError, match="Choose a mode"):
        assess_manual_geometry(None, 100.0, "source", description)
    with pytest.raises(ValueError, match="not requested mode 'narrow'"):
        assess_manual_geometry(None, 100.0, "source", description, "narrow")
    _, _, _, assessment = assess_manual_geometry(
        None, 100.0, "source", description, "wide"
    )
    assert assessment["geometry_status"] == "supported"
    assert assessment["geometry_mode"] == "wide"


@pytest.mark.parametrize("reverse_order", [False, True])
def test_mode_assessment_scopes_evidence_and_selected_candidate(reverse_order):
    narrow = "Narrow mode swath 25 km has gaps between its tracks."
    wide = (
        "Wide mode swath 100 km is a continuous nadir-centered swath with no "
        "gaps or separate pointing needed."
    )
    description = " ".join((wide, narrow) if reverse_order else (narrow, wide))
    candidates = extract_geometry_candidates({"Modes": description})

    narrow_assessment = assess_geometry_candidates(
        candidates,
        context=description,
        geometry_kind="swath_width_km",
        geometry_value=25.0,
        geometry_mode="narrow",
    )
    assert narrow_assessment["geometry_status"] == "needs_clarification"
    assert narrow_assessment["selected_candidate"]["mode"] == "narrow"
    assert narrow_assessment["selected_candidate"]["value"] == 25.0

    wide_assessment = assess_geometry_candidates(
        candidates,
        context=description,
        geometry_kind="swath_width_km",
        geometry_value=100.0,
        geometry_mode="wide",
    )
    assert wide_assessment["geometry_status"] == "supported"
    assert wide_assessment["selected_candidate"]["mode"] == "wide"
    assert wide_assessment["selected_candidate"]["value"] == 100.0

    with pytest.raises(ValueError, match="requires clarification"):
        assess_manual_geometry(None, 25.0, "source", description, "narrow")


def test_same_width_in_multiple_modes_still_requires_explicit_mode():
    description = (
        "Narrow mode swath 50 km. Wide mode swath 50 km. Continuous "
        "nadir-centered swath with no gaps or separate pointing needed."
    )
    candidates = extract_geometry_candidates({"Modes": description})
    assert resolve_geometry_status(candidates)[0] == "needs_clarification"
    with pytest.raises(ValueError, match="Choose a mode"):
        assess_manual_geometry(None, 50.0, "source", description)


def test_conflicting_values_within_one_mode_require_clarification():
    description = (
        "Wide mode swath 25 km and wide mode swath 100 km; continuous "
        "nadir-centered swath with no gaps or separate pointing needed."
    )
    with pytest.raises(ValueError, match="Conflicting values"):
        assess_manual_geometry(None, 100.0, "source", description, "wide")


def test_six_separated_laser_tracks_are_an_automatic_approximation():
    candidates = extract_geometry_candidates(
        {
            "Instrument description": (
                "Six separated laser tracks with gaps span a 6.6 km swath."
            )
        }
    )
    assert candidates and candidates[0]["value"] == 6.6
    status, reason, recommended = resolve_geometry_status(candidates)
    assert status == "approximate"
    assert recommended["value"] == 6.6
    assert "gaps between tracks" in reason
    assert "accessible" in reason


def test_any_separated_tracks_with_a_documented_overall_span_are_approximate():
    description = (
        "Four separated laser tracks with gaps span an overall swath width "
        "of 7.0 km."
    )
    candidates = extract_geometry_candidates({"Instrument description": description})
    status, reason, recommended = resolve_geometry_status(candidates, description)
    assert status == "approximate"
    assert recommended["value"] == 7.0
    assert "gaps between tracks" in reason
    assert "overall span" in reason


def test_track_assertion_and_overall_width_can_come_from_separate_fields():
    source_fields = {
        "Scanning": "Six separated laser tracks with gaps.",
        "Coverage": "Overall swath width 6.6 km.",
    }
    candidates = extract_geometry_candidates(source_fields)
    resolver = assess_geometry_candidates(candidates, context=source_fields)
    manual_description = (
        "Six separated laser tracks with gaps. Overall swath width 6.6 km."
    )
    manual = assess_manual_geometry(
        None, 6.6, "source", manual_description
    )[3]

    assert resolver["geometry_status"] == "approximate"
    assert manual["geometry_status"] == "approximate"
    assert resolver["warnings"] == manual["warnings"]
    assert resolver["selected_candidate"]["source_field"] == "Coverage"
    assert resolver["selected_candidate"]["source_text"] == "Overall swath width 6.6 km."


def test_per_track_width_does_not_establish_an_overall_envelope():
    description = "Four separated tracks have gaps; each track is 2 km wide."
    candidates = extract_geometry_candidates({"Instrument description": description})
    assert not [candidate for candidate in candidates if candidate["kind"] == "swath_width_km"]
    with pytest.raises(ValueError, match="does not match a candidate"):
        assess_manual_geometry(None, 2.0, "source", description)


def test_negative_and_contradictory_geometry_assertions_need_clarification():
    descriptions = (
        "Swath 290 km. Not continuous, not nadir-centered, not gap-free. "
        "No separate pointing needed.",
        "Continuous nadir-centered swath 290 km, no gaps, no separate pointing "
        "needed. However, this instrument measures separated tracks with gaps "
        "between them.",
    )
    for description in descriptions:
        candidates = extract_geometry_candidates({"Scanning Technique": description})
        assessment = assess_geometry_candidates(candidates, context=description)
        assert assessment["geometry_status"] == "needs_clarification"


def test_without_steering_is_positive_evidence_not_unclear_steering():
    description = (
        "Continuous nadir-centered swath 290 km, no gaps, without steering."
    )
    candidates = extract_geometry_candidates({"Scanning Technique": description})
    assert resolve_geometry_status(candidates, description)[0] == "supported"


@pytest.mark.parametrize(
    "description",
    [
        (
            "Swath 290 km. Not necessarily continuous or nadir-centered. "
            "It is gap-free without steering."
        ),
        (
            "Swath 290 km. It may be continuous and nadir-centered. "
            "It is gap-free without steering."
        ),
    ],
)
def test_qualified_or_modal_support_requires_clarification(description):
    candidates = extract_geometry_candidates({"Scanning Technique": description})
    assessment = assess_geometry_candidates(candidates, context=description)
    assert assessment["geometry_status"] == "needs_clarification"


def test_candidate_limit_cannot_hide_a_later_conflicting_measurement():
    specs = {
        f"geometry field {index}": (
            "Continuous nadir-centered swath 290 km, no gaps, no separate "
            "pointing needed."
        )
        for index in range(10)
    }
    specs["late conflicting geometry"] = "Swath 100 km."
    candidates = extract_geometry_candidates(specs)
    assert len(candidates) == 11
    assessment = assess_geometry_candidates(candidates, context=specs)
    assert assessment["geometry_status"] == "needs_clarification"
    assert "Conflicting values" in assessment["interpretation"]


def test_non_nadir_warning_in_another_spec_field_blocks_support():
    specs = {
        "Swath": "Continuous swath 290 km",
        "Pointing": "Instrument uses off-nadir pointing.",
    }
    candidates = extract_geometry_candidates(specs)
    status, reason, recommended = resolve_geometry_status(candidates, specs)
    assert status == "approximate"
    assert recommended["value"] == 290.0
    assert "off-nadir" in reason.lower()
    assert "nadir-centered" in reason.lower()


def test_explicitly_no_off_nadir_pointing_can_support_nadir_geometry():
    specs = {
        "Swath": (
            "Continuous nadir-centered swath 290 km with no gaps and no "
            "off-nadir pointing needed."
        )
    }
    candidates = extract_geometry_candidates(specs)
    status, _, _ = resolve_geometry_status(candidates, specs)
    assert status == "supported"


def test_empty_specs_require_clarification():
    for specs in (None, {}, {"Resolution": "10 m"}):
        status, reason, recommended = resolve_geometry_status(
            extract_geometry_candidates(specs)
        )
        assert status == "needs_clarification"
        assert recommended is None
        assert "No swath width" in reason
    assert extract_geometry_candidates("swath 290 km") == []  # type: ignore[arg-type]


def test_validate_requires_reference_and_exactly_one_geometry():
    with pytest.raises(ValueError, match="geometry_reference is required"):
        validate_geometry_inputs(50.0, None, "")
    with pytest.raises(ValueError, match="exactly one"):
        validate_geometry_inputs(50.0, 290.0, "https://example.com/msi")
    with pytest.raises(ValueError, match="Missing geometry"):
        validate_geometry_inputs(None, None, "https://example.com/msi")


@pytest.mark.parametrize("bad", [0, -5, 361, float("nan"), float("inf"), True, "wide"])
def test_validate_rejects_bad_for(bad):
    with pytest.raises(ValueError, match="field_of_regard_deg"):
        validate_geometry_inputs(bad, None, "https://example.com/x")


@pytest.mark.parametrize("bad", [0, -10, float("nan"), float("inf"), True, "wide", 25000])
def test_validate_rejects_bad_swath(bad):
    with pytest.raises(ValueError, match="swath_width_km"):
        validate_geometry_inputs(None, bad, "https://example.com/x")


def test_validate_accepts_either_shape():
    assert validate_geometry_inputs(110.0, None, "https://example.com/modis") == (
        110.0, None, "https://example.com/modis",
    )
    assert validate_geometry_inputs(None, 290.0, "https://example.com/msi") == (
        None, 290.0, "https://example.com/msi",
    )


def test_swath_round_trips_through_for():
    for_deg = convert_swath_to_for(290.0, 786_000.0)
    assert 0 < for_deg < 60
    swath_m = field_of_regard_to_swath_width(786_000.0, for_deg)
    assert swath_m / 1000.0 == pytest.approx(290.0, rel=0.01)


@pytest.mark.parametrize(
    "swath, altitude",
    [(0, 700_000.0), (-5, 700_000.0), (290.0, 0), (290.0, -100.0)],
)
def test_convert_rejects_non_positive(swath, altitude):
    with pytest.raises(ValueError, match="must be positive"):
        convert_swath_to_for(swath, altitude)
