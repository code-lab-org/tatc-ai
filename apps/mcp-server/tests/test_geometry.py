"""Geometry resolver tests: parsing, status, validation, conversion."""

import pytest
from tatc.utils import field_of_regard_to_swath_width

from src.geometry import (
    convert_swath_to_for,
    extract_geometry_candidates,
    resolve_geometry_status,
    validate_geometry_inputs,
)


def test_msi_style_swath_is_conclusive():
    specs = {"Scanning Technique": "Pushbroom. Swath 290 km"}
    candidates = extract_geometry_candidates(specs)
    assert len(candidates) == 1
    [candidate] = candidates
    assert candidate["kind"] == "swath_width_km"
    assert candidate["value"] == 290.0
    assert candidate["confidence"] == "high"
    assert candidate["usable_for_coverage"] is True
    status, _, recommended = resolve_geometry_status(candidates)
    assert status == "conclusive"
    assert recommended["value"] == 290.0


def test_modis_half_angle_is_derived_and_needs_review():
    specs = {"Scanning Technique": "Cross-track scan, fixed \xb155-degree viewing angle"}
    candidates = extract_geometry_candidates(specs)
    derived = [c for c in candidates if c["kind"] == "field_of_regard_deg"]
    assert derived and derived[0]["value"] == 110.0
    assert derived[0]["derived"] is True
    # A bare half angle must not silently become coverage geometry.
    assert all(c["usable_for_coverage"] is False for c in candidates)
    status, reason, recommended = resolve_geometry_status(candidates)
    assert status == "needs_review"
    assert recommended is None
    assert "simple cone" in reason


def test_steerable_swath_needs_review():
    specs = {
        "Scanning Technique": "Pushbroom; swath 25 km steerable within "
        "a field of regard of 300 km"
    }
    candidates = extract_geometry_candidates(specs)
    assert candidates
    assert all(c["usable_for_coverage"] is False for c in candidates)
    status, _, _ = resolve_geometry_status(candidates)
    assert status == "needs_review"


def test_limb_sounder_needs_review():
    specs = {"Scanning Technique": "Limb viewing with 63 km swath."}
    candidates = extract_geometry_candidates(specs)
    assert candidates and candidates[0]["kind"] == "swath_width_km"
    assert candidates[0]["needs_review"] is True
    assert candidates[0]["usable_for_coverage"] is False
    status, _, _ = resolve_geometry_status(candidates)
    assert status == "needs_review"


def test_conical_scanner_swath_is_usable_but_side_looking_radar_is_not():
    conical = extract_geometry_candidates(
        {"Scanning Technique": "Conical: 55\xb0 zenith angle; swath: 1450 km"}
    )
    assert resolve_geometry_status(conical)[0] == "conclusive"
    side = extract_geometry_candidates(
        {"Scanning Technique": "Side-looking, 15-45\xb0 off-nadir, 250 km swath"}
    )
    assert side and side[0]["usable_for_coverage"] is False
    assert resolve_geometry_status(side)[0] == "needs_review"


def test_fov_reports_need_review_not_silent_use():
    specs = {"Swath": "FOV: 114 degree (cross track)."}
    candidates = extract_geometry_candidates(specs)
    assert candidates and candidates[0]["kind"] == "field_of_view_deg"
    assert candidates[0]["usable_for_coverage"] is False
    status, _, _ = resolve_geometry_status(candidates)
    assert status == "needs_review"


def test_conflicting_swaths_are_ambiguous():
    specs = {
        "Scanning Technique": "Pushbroom. Swath 290 km",
        "Coverage / Cycle": "Wide mode swath 890 km",
    }
    candidates = extract_geometry_candidates(specs)
    usable = [c for c in candidates if c["usable_for_coverage"]]
    assert len(usable) == 2
    status, reason, recommended = resolve_geometry_status(candidates)
    assert status == "ambiguous"
    assert recommended is None
    assert "different values" in reason


def test_repeated_same_swath_stays_conclusive():
    specs = {
        "Scanning Technique": "Pushbroom. Swath 290 km",
        "Coverage / Cycle": "Swath 290 km total",
    }
    status, _, recommended = resolve_geometry_status(
        extract_geometry_candidates(specs)
    )
    assert status == "conclusive"
    assert recommended["value"] == 290.0


def test_empty_specs_is_unknown():
    for specs in (None, {}, {"Resolution": "10 m"}):
        status, reason, recommended = resolve_geometry_status(
            extract_geometry_candidates(specs)
        )
        assert status == "unknown"
        assert recommended is None
        assert "Provide swath_width_km" in reason
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
