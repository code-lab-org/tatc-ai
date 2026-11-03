"""Numeric extraction and structured assessment validation."""

import pytest
from tatc.utils import field_of_regard_to_swath_width

from src.geometry import (
    GeometryAssessment,
    convert_swath_to_for,
    extract_geometry_candidates,
    numeric_certainty,
    validate_geometry_inputs,
)


def _assessment(value=290, status="supported", unit="km", **extra):
    result = {
        "status": status,
        "source_text": f"Swath {value} {unit}.",
        "explanation": "The assistant selected this source value for the study.",
    }
    result.update(extra)
    if status == "approximate" and "warning" not in result:
        result["warning"] = "The model fills the gaps between separate tracks."
    return result


def test_numeric_extraction_keeps_provenance_without_classifying_compatibility():
    sources = {
        "Swath": "Swath 290 km. Off-nadir pointing is used.",
        "Coverage": "Overall swath width 290 km.",
    }
    candidates = extract_geometry_candidates(sources)

    assert [(item["kind"], item["value"]) for item in candidates] == [
        ("swath_width_km", 290.0),
        ("swath_width_km", 290.0),
    ]
    assert [item["source_field"] for item in candidates] == ["Swath", "Coverage"]
    assert candidates[0]["source_text"] == sources["Swath"]
    assert numeric_certainty(candidates) == "high"
    assert all("status" not in item for item in candidates)


def test_numeric_extraction_marks_conflicting_numbers_ambiguous():
    candidates = extract_geometry_candidates(
        {"Narrow mode": "Swath 25 km.", "Wide mode": "Swath 100 km."}
    )
    assert numeric_certainty(candidates) == "ambiguous"


@pytest.mark.parametrize(
    "source",
    [
        "Swath -290 km.",
        "Swath +290 km.",
        "Swath 25-290 km.",
        "Swath 25 to 290 km.",
    ],
)
def test_numeric_extraction_does_not_turn_signed_or_ranged_widths_into_scalars(source):
    assert extract_geometry_candidates({"Scanning": source}) == []


def test_assessment_status_and_selected_mode_are_preserved():
    value = validate_geometry_inputs(
        None,
        100,
        "OSCAR source",
        _assessment(
            100,
            "supported",
            source_text="Wide mode: swath 100 km.",
            mode="wide",
        ),
    )
    assert value[:3] == (None, 100.0, "OSCAR source")
    assert value[3].status == "supported"
    assert value[3].mode == "wide"


def test_validator_checks_numeric_token_but_does_not_parse_compatibility_prose():
    assessment = _assessment(
        290,
        source_text="Swath 290 km. Off-nadir pointing may be used.",
        explanation="The assistant determined that this source supports the model.",
    )
    _, _, _, validated = validate_geometry_inputs(
        None, 290, "source", assessment
    )
    assert validated.status == "supported"
    assert validated.source_text == assessment["source_text"]


def test_source_number_matches_when_unit_has_no_space():
    assessment = _assessment(290, source_text="The documented swath is 290km.")
    _, swath, _, _ = validate_geometry_inputs(None, 290, "source", assessment)
    assert swath == 290.0


@pytest.mark.parametrize(
    "field_of_regard_deg, swath_width_km, source_text",
    [
        (50, None, "Field of regard 50 km."),
        (None, 290, "Swath 290 degrees."),
        (50, None, "Field of regard 50"),
        (None, 290, "Swath 290"),
        (50, None, "Value 50; another angle is 51 degrees."),
        (None, 290, "Value 290; another swath is 291 km."),
    ],
)
def test_validator_requires_unit_associated_with_selected_value(
    field_of_regard_deg, swath_width_km, source_text
):
    value = field_of_regard_deg or swath_width_km
    assessment = _assessment(value, source_text=source_text)
    with pytest.raises(ValueError, match="selected numeric value"):
        validate_geometry_inputs(
            field_of_regard_deg,
            swath_width_km,
            "source",
            assessment,
        )


@pytest.mark.parametrize(
    "field_of_regard_deg, swath_width_km, source_text",
    [
        (50, None, "Value 50 degrees."),
        (50, None, "Value 50degree."),
        (50, None, "Value 50 deg."),
        (50, None, "Value 50deg."),
        (50, None, "Value 50 °."),
        (50, None, "Value 50°."),
        (50, None, "Value 50 DEGREES."),
        (None, 290, "Swath 290 km."),
        (None, 290, "Swath 290km."),
        (None, 290, "Swath 290 KM."),
    ],
)
def test_validator_accepts_common_source_unit_spellings(
    field_of_regard_deg, swath_width_km, source_text
):
    value = field_of_regard_deg or swath_width_km
    unit = "degrees" if field_of_regard_deg is not None else "km"
    assessment = _assessment(value, unit=unit, source_text=source_text)
    result = validate_geometry_inputs(
        field_of_regard_deg,
        swath_width_km,
        "source",
        assessment,
    )
    assert result[0] == field_of_regard_deg
    assert result[1] == swath_width_km


@pytest.mark.parametrize(
    "assessment, message",
    [
        (None, "Invalid geometry_assessment"),
        (_assessment(290, "needs_clarification"), "requires clarification"),
        (_assessment(290, "approximate", warning="  "), "non-empty"),
        (_assessment(290, "supported", warning="approximation"), "cannot include"),
        (_assessment(290, source_text="Swath 291 km."), "selected numeric value"),
        (_assessment(290, source_text="Swath 290 km.", extra="invalid"), "extra"),
        (_assessment(290, explanation="  "), "non-empty"),
    ],
)
def test_validator_rejects_invalid_assessment(assessment, message):
    with pytest.raises(ValueError, match=message):
        validate_geometry_inputs(None, 290, "source", assessment)


@pytest.mark.parametrize("bad", [0, -5, 361, float("nan"), float("inf"), True, "wide"])
def test_validator_rejects_bad_field_of_regard(bad):
    with pytest.raises(ValueError, match="field_of_regard_deg"):
        validate_geometry_inputs(
            bad, None, "source", _assessment(50, unit="degrees")
        )


@pytest.mark.parametrize("bad", [0, -10, float("nan"), float("inf"), True, "wide", 25000])
def test_validator_rejects_bad_swath(bad):
    with pytest.raises(ValueError, match="swath_width_km"):
        validate_geometry_inputs(None, bad, "source", _assessment(290))


def test_validator_requires_citation_and_exactly_one_geometry():
    with pytest.raises(ValueError, match="geometry_reference is required"):
        validate_geometry_inputs(
            50, None, "", _assessment(50, unit="degrees")
        )
    with pytest.raises(ValueError, match="exactly one"):
        validate_geometry_inputs(
            50, 290, "source", _assessment(50, unit="degrees")
        )
    with pytest.raises(ValueError, match="exactly one"):
        validate_geometry_inputs(None, None, "source", _assessment())


def test_geometry_assessment_schema_rejects_unknown_fields():
    with pytest.raises(ValueError, match="extra"):
        GeometryAssessment.model_validate(_assessment(290, surprise="x"))


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
