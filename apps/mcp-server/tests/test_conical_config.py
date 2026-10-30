"""Typed conical configuration: source evidence, conflicts, and rejection."""

import pytest
from pydantic import ValidationError

from src.conical_config import ConicalConfiguration, configuration_from_snapshot
from src.conical_tatc import resolved_snapshot
from tests.conical_fixtures import load_study_config
OMM = {
    "OBJECT_NAME": "GCOM-W1 (SHIZUKU)",
    "OBJECT_ID": "2012-025A",
    "EPOCH": "2026-10-04T07:07:22.400000",
    "MEAN_MOTION": 14.57111943764929,
    "ECCENTRICITY": 0.0001644,
    "INCLINATION": 98.2100,
    "RA_OF_ASC_NODE": 216.2278,
    "ARG_OF_PERICENTER": 77.4488,
    "MEAN_ANOMALY": 41.8708,
    "EPHEMERIS_TYPE": 0,
    "CLASSIFICATION_TYPE": "U",
    "NORAD_CAT_ID": 38337,
    "ELEMENT_SET_NO": 999,
    "REV_AT_EPOCH": 0,
    "BSTAR": 0.000063281,
    "MEAN_MOTION_DOT": 0.00000240,
    "MEAN_MOTION_DDOT": 0,
}


def _fixture():
    return load_study_config()


def _valid():
    return ConicalConfiguration.model_validate(_fixture())


def test_amsr2_fixture_is_typed_and_source_backed():
    configuration = _valid()
    assert configuration.configuration_id == "amsr2-gcom-w1-conical-product-sector-study"
    assert configuration.revision == "2026-10-27.1-study"
    assert configuration.spacecraft.norad_id == 38337
    assert configuration.operating_mode == "conical_scan_40_rpm_product_sector"
    assert configuration.channel.center_frequency_ghz == 10.65
    assert configuration.geometry.cone_angle.value == 47.564
    assert configuration.geometry.cone_angle.unit == "degree"
    assert configuration.geometry.scan_center_azimuth.value == 0.498
    assert configuration.geometry.scan_half_width.value == 75.332
    assert configuration.geometry.along_track_field_of_view.value == 1.0
    assert configuration.geometry.velocity_frame == "inertial"
    assert configuration.geometry.nadir_reference == "geodetic"
    assert configuration.approximation_status == "approximate"
    assert any("nominal" in conflict.description.lower() for conflict in configuration.source_conflicts)
    assert any(
        conflict.conflict_id == "incidence-angle-vs-cone-angle"
        for conflict in configuration.source_conflicts
    )
    assert configuration.applicability.start_utc.year == 2012
    assert configuration.applicability.end_utc_exclusive.year == 2026
    assert configuration.applicability.start_utc < configuration.applicability.end_utc_exclusive
    assert len(configuration.evidence) >= 8
    assert all(item.locator and item.excerpt for item in configuration.evidence)
    assert all(check.status in {"passed", "limited"} for check in configuration.geometry_checks)
    assert all(check.geometry_parameter is None for check in configuration.geometry_checks)
    assert any(check.check_id == "orbit-reference-residual" for check in configuration.geometry_checks)


def test_snapshot_round_trips_through_configuration():
    configuration = _valid()
    snapshot = resolved_snapshot(configuration, [OMM])
    rebuilt = configuration_from_snapshot(snapshot)
    assert rebuilt.configuration_id == configuration.configuration_id
    assert rebuilt.geometry.scan_half_width.value == 75.332
    assert snapshot["tatc_mapping"]["tatc_version"] == "3.6.0"
    assert snapshot["tatc_mapping"]["field_of_regard_deg"] == pytest.approx(98.128)
    assert snapshot["tatc_mapping"]["modeled_swath_width_km_at_omm_mean_altitude"] > 1500
    assert snapshot["software"]["python"].startswith("3.12")


def test_missing_geometry_evidence_is_rejected():
    data = _fixture()
    data["evidence"] = [
        item
        for item in data["evidence"]
        if item["evidence_id"] != "tatc-amsr2-nadir-reference"
    ]
    data["source_revision"]["evidence_ids"].remove("tatc-amsr2-nadir-reference")
    with pytest.raises(ValidationError, match="missing source evidence"):
        ConicalConfiguration.model_validate(data)


@pytest.mark.parametrize("field", ["excerpt", "locator", "authority", "document_revision"])
def test_blank_source_evidence_is_rejected(field):
    data = _fixture()
    data["evidence"][0][field] = " \t\n "
    with pytest.raises(ValidationError):
        ConicalConfiguration.model_validate(data)


def test_missing_illumination_evidence_is_rejected():
    data = _fixture()
    data["evidence"] = [
        item
        for item in data["evidence"]
        if item["evidence_id"] != "jaxa-amsr2-passive-microwave"
    ]
    data["source_revision"]["evidence_ids"].remove("jaxa-amsr2-passive-microwave")
    with pytest.raises(ValidationError, match="geometry.illumination_requirement"):
        ConicalConfiguration.model_validate(data)


def test_invalid_angle_unit_is_rejected():
    data = _fixture()
    data["geometry"]["cone_angle"]["unit"] = "rad"
    with pytest.raises(ValidationError, match="angle unit must be degrees"):
        ConicalConfiguration.model_validate(data)


def test_nonfinite_angle_is_rejected():
    data = _fixture()
    data["geometry"]["cone_angle"]["value"] = float("inf")
    with pytest.raises(ValidationError):
        ConicalConfiguration.model_validate(data)


def test_boolean_angle_is_rejected():
    data = _fixture()
    data["geometry"]["cone_angle"]["value"] = True
    with pytest.raises(ValidationError, match="boolean"):
        ConicalConfiguration.model_validate(data)


def test_unsupported_model_family_is_rejected():
    data = _fixture()
    data["geometry"]["model_family"] = "pointed"
    with pytest.raises(ValidationError, match="unsupported geometry model family"):
        ConicalConfiguration.model_validate(data)


def test_incompatible_extra_field_is_rejected():
    data = _fixture()
    data["geometry"]["cross_track_field_of_view"] = {"value": 10.0, "unit": "degree"}
    with pytest.raises(ValidationError, match="Extra inputs"):
        ConicalConfiguration.model_validate(data)


def test_unresolved_source_conflict_is_rejected():
    data = _fixture()
    data["source_conflicts"][0]["chosen_option_id"] = None
    data["source_conflicts"][0]["rationale"] = None
    with pytest.raises(ValidationError, match="unresolved"):
        ConicalConfiguration.model_validate(data)


def test_chosen_sector_cannot_be_combined_with_other_width():
    data = _fixture()
    data["source_conflicts"][0]["chosen_option_id"] = "nominal-61deg"
    with pytest.raises(ValidationError, match="do not combine"):
        ConicalConfiguration.model_validate(data)


def test_ground_incidence_choice_requires_an_explicit_cone_mapping():
    data = _fixture()
    conflict = data["source_conflicts"][1]
    conflict["chosen_option_id"] = "ground-incidence-55deg"
    data["geometry"]["cone_angle"]["value"] = 55.0
    with pytest.raises(ValidationError, match="incidence.*cone"):
        ConicalConfiguration.model_validate(data)


def test_ground_incidence_choice_accepts_a_separate_sourced_cone_mapping():
    data = _fixture()
    conflict = data["source_conflicts"][1]
    conflict["chosen_option_id"] = "ground-incidence-55deg"
    conflict["options"][0]["cone_angle_deg"] = 47.564
    conflict["options"][0]["evidence_ids"].append("tatc-amsr2-validation")
    configuration = ConicalConfiguration.model_validate(data)
    assert configuration.geometry.cone_angle.value == 47.564


def test_ground_incidence_mapping_requires_cone_evidence_on_the_selected_option():
    data = _fixture()
    conflict = data["source_conflicts"][1]
    conflict["chosen_option_id"] = "ground-incidence-55deg"
    conflict["options"][0]["cone_angle_deg"] = 47.564
    with pytest.raises(ValidationError, match="cone.*evidence"):
        ConicalConfiguration.model_validate(data)


def test_declared_approximation_cannot_claim_supported_status():
    data = _fixture()
    data["approximation_status"] = "supported"
    with pytest.raises(ValidationError, match="must use approximation_status"):
        ConicalConfiguration.model_validate(data)


def test_failed_geometry_check_rejects_configuration():
    data = _fixture()
    data["geometry_checks"][0]["status"] = "failed"
    with pytest.raises(ValidationError, match="failed"):
        ConicalConfiguration.model_validate(data)


def test_unknown_evidence_reference_is_rejected():
    data = _fixture()
    data["applicability"]["evidence_ids"].append("missing-evidence")
    with pytest.raises(ValidationError, match="unknown evidence_id"):
        ConicalConfiguration.model_validate(data)


def test_multiple_configurations_are_rejected():
    data = _fixture()
    with pytest.raises(ValidationError, match="exactly one conical configuration"):
        ConicalConfiguration.model_validate([data])


def test_fixture_evidence_contains_exact_source_values():
    data = _fixture()
    excerpts = " ".join(item["excerpt"] for item in data["evidence"])
    assert "47.56" in excerpts
    assert "75.3" in excerpts
    assert "inertial velocity" in excerpts
    assert "+/-61" in excerpts
    assert "+/-75" in excerpts
    assert "1.7 km along track" in excerpts
    assert "0.27 s" in excerpts
    assert "August 10" in excerpts
