"""Typed, evidence-backed settings for side-looking pointed instruments."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, ValidationError, model_validator

from .conical_config import (
    Angle,
    ApplicabilityInterval,
    Approximation,
    ChannelIdentity,
    Interpretation,
    SourceConflict,
    SourceEvidence,
    SourceRevision,
    SpacecraftIdentity,
    iso_utc,
)

SCHEMA_VERSION = "pointed-configuration-v1"

REQUIRED_GEOMETRY_SUPPORT = {
    "identity.instrument",
    "identity.spacecraft",
    "identity.operating_mode",
    "identity.channel",
    "geometry.look_direction",
    "geometry.off_nadir_ranges",
    "geometry.incidence_ranges",
    "geometry.field_of_regard",
    "geometry.cross_track_field_of_view",
    "geometry.along_track_field_of_view",
    "geometry.roll_attitude",
    "geometry.reference_frame",
    "geometry.nadir_reference",
    "geometry.velocity_frame",
    "geometry.view_geometry",
    "geometry.source_altitude_range",
    "geometry.azimuth_beam_width",
    "geometry.azimuth_steering_range",
    "geometry.roll_steering",
    "geometry.iw_subswath_angles",
    "applicability.start",
    "applicability.end",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class AngleRange(_StrictModel):
    """An ordered interval whose endpoints retain their angle units."""

    minimum: Angle
    maximum: Angle

    @model_validator(mode="after")
    def _check_range(self) -> "AngleRange":
        if self.minimum.unit != self.maximum.unit:
            raise ValueError("angle range endpoints must use the same unit")
        if self.minimum.value >= self.maximum.value:
            raise ValueError("angle range minimum must be less than maximum")
        return self


class SubswathAngleReference(_StrictModel):
    """Published angle bounds for one IW subswath and orbit-altitude case."""

    subswath: Literal["IW1", "IW2", "IW3"]
    altitude_reference: Literal["minimum", "maximum"]
    satellite_altitude_km: StrictFloat = Field(gt=0)
    off_nadir: AngleRange
    incidence: AngleRange
    evidence_ids: List[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_angle_distinction(self) -> "SubswathAngleReference":
        if self.off_nadir.minimum.value == self.incidence.minimum.value and (
            self.off_nadir.maximum.value == self.incidence.maximum.value
        ):
            raise ValueError("off-nadir and incidence angles must remain distinct")
        return self


class AttitudeAssumptions(_StrictModel):
    """Explicit body attitude assumptions used by the TAT-C mapping."""

    yaw_reference: Literal["zero_doppler"]
    roll_profile: Literal["fixed"]
    roll_assumption: str = Field(min_length=1)
    pitch_assumption: str = Field(min_length=1)
    roll_angle: Angle
    pitch_angle: Angle


class PointedGeometry(_StrictModel):
    """Side-looking frame geometry and its source-specific IW context."""

    model_family: Literal["pointed"] = "pointed"
    look_direction: Literal["left", "right"]
    reference_frame: Literal["earth_fixed_velocity_frame"]
    off_nadir_reference: Literal["geodetic_nadir"]
    incidence_reference: Literal["ellipsoid_surface_normal"]
    off_nadir_view_range: AngleRange
    incidence_view_range: AngleRange
    field_of_regard: Angle
    field_of_regard_margin: Angle = Field(
        description=(
            "The one-sided envelope margin added beyond the far off-nadir edge "
            "before doubling it into a total field of regard."
        )
    )
    off_nadir_source_precision_deg: StrictFloat = Field(gt=0, le=1)
    cross_track_field_of_view: Angle
    along_track_field_of_view: Angle
    roll_angle: Angle
    pitch_angle: Angle
    nadir_reference: Literal["geodetic", "geocentric"]
    velocity_frame: Literal["inertial", "earth_fixed"]
    view_geometry: Literal["frame"]
    is_rectangular: Literal[True]
    along_track_view_definition: Literal["full_azimuth_antenna_beam_width"]
    attitude: AttitudeAssumptions
    source_altitude_minimum_km: StrictFloat = Field(gt=0)
    source_altitude_maximum_km: StrictFloat = Field(gt=0)
    observed_roll_range: AngleRange
    minimum_ground_swath_width_km: StrictFloat = Field(gt=0)
    azimuth_beam_width: Angle
    azimuth_steering_range: AngleRange
    roll_steering_reference_altitude_km: StrictFloat = Field(gt=0)
    maximum_roll_steering_delta_deg: StrictFloat = Field(gt=0)
    source_subswath_angles: List[SubswathAngleReference] = Field(min_length=6)
    geometry_evidence_ids: List[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_mapping(self) -> "PointedGeometry":
        if self.source_altitude_minimum_km >= self.source_altitude_maximum_km:
            raise ValueError("source altitude minimum must be less than maximum")
        if self.observed_roll_range.minimum.unit != "degree":
            raise ValueError("observed_roll_range must use degrees")
        if not (
            self.observed_roll_range.minimum.value
            <= abs(self.roll_angle.value)
            <= self.observed_roll_range.maximum.value
        ):
            raise ValueError(
                "the fixed roll magnitude must lie inside the published observed roll range"
            )
        if self.field_of_regard.unit != "degree":
            raise ValueError("field_of_regard must use degrees")
        if not 0 < self.field_of_regard.value <= 180:
            raise ValueError("field_of_regard must be greater than 0 and at most 180 degrees")
        if self.cross_track_field_of_view.unit != "degree":
            raise ValueError("cross_track_field_of_view must use degrees")
        if not 0 < self.cross_track_field_of_view.value < 180:
            raise ValueError(
                "cross_track_field_of_view must be greater than 0 and less than 180 degrees"
            )
        if self.along_track_field_of_view.unit != "degree":
            raise ValueError("along_track_field_of_view must use degrees")
        if not 0 < self.along_track_field_of_view.value < 180:
            raise ValueError(
                "along_track_field_of_view must be greater than 0 and less than 180 degrees"
            )
        if self.roll_angle.unit != "degree" or self.pitch_angle.unit != "degree":
            raise ValueError("roll_angle and pitch_angle must use degrees")
        if self.azimuth_beam_width.unit != "degree":
            raise ValueError("azimuth_beam_width must use degrees")
        if not math.isclose(
            self.azimuth_beam_width.value,
            self.along_track_field_of_view.value,
            rel_tol=0,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "along_track_field_of_view must retain the sourced full azimuth "
                "antenna beam width used by this mapping"
            )
        if self.geometry_evidence_ids == []:
            raise ValueError("geometry parameters require source evidence")
        if self.velocity_frame != "earth_fixed":
            raise ValueError("this pointed mapping requires an Earth-fixed velocity frame")
        if self.attitude.roll_angle != self.roll_angle:
            raise ValueError("attitude.roll_angle must match geometry.roll_angle")
        if self.attitude.pitch_angle != self.pitch_angle:
            raise ValueError("attitude.pitch_angle must match geometry.pitch_angle")
        if self.look_direction == "right" and self.roll_angle.value >= 0:
            raise ValueError(
                "TAT-C's positive roll convention points left of motion; "
                "right-looking geometry requires a negative roll angle"
            )
        if self.look_direction == "left" and self.roll_angle.value <= 0:
            raise ValueError(
                "TAT-C's positive roll convention points left of motion; "
                "left-looking geometry requires a positive roll angle"
            )
        near = abs(self.roll_angle.value) - self.cross_track_field_of_view.value / 2
        far = abs(self.roll_angle.value) + self.cross_track_field_of_view.value / 2
        if near <= 0 or far >= 90:
            raise ValueError("the mapped off-nadir interval must remain between 0 and 90 degrees")
        if (
            abs(near - self.off_nadir_view_range.minimum.value)
            > self.off_nadir_source_precision_deg
            or abs(far - self.off_nadir_view_range.maximum.value)
            > self.off_nadir_source_precision_deg
        ):
            raise ValueError(
                "roll_angle and cross_track_field_of_view do not match the "
                "declared off-nadir view range within its published precision"
            )
        if self.field_of_regard_margin.unit != "degree" or self.field_of_regard_margin.value < 0:
            raise ValueError("field_of_regard_margin must be a non-negative degree angle")
        field_of_regard = 2 * (
            self.off_nadir_view_range.maximum.value
            + self.field_of_regard_margin.value
        )
        if not math.isclose(
            field_of_regard,
            self.field_of_regard.value,
            rel_tol=0,
            abs_tol=1e-6,
        ):
            raise ValueError(
                "field_of_regard must equal twice the maximum modeled off-nadir angle"
            )
        if self.azimuth_steering_range.minimum.value >= 0 or (
            self.azimuth_steering_range.maximum.value <= 0
        ):
            raise ValueError("azimuth steering range must cross zero")
        if not (
            self.source_altitude_minimum_km
            <= self.roll_steering_reference_altitude_km
            <= self.source_altitude_maximum_km
        ):
            raise ValueError(
                "roll steering reference altitude must be inside the sourced altitude range"
            )
        pairs = {(row.altitude_reference, row.subswath) for row in self.source_subswath_angles}
        expected_pairs = {
            (altitude, subswath)
            for altitude in ("minimum", "maximum")
            for subswath in ("IW1", "IW2", "IW3")
        }
        if pairs != expected_pairs or len(self.source_subswath_angles) != len(expected_pairs):
            raise ValueError(
                "IW geometry must retain one angle range for each of three "
                "subswaths at both altitude references"
            )
        for row in self.source_subswath_angles:
            expected_altitude = (
                self.source_altitude_minimum_km
                if row.altitude_reference == "minimum"
                else self.source_altitude_maximum_km
            )
            if not math.isclose(
                row.satellite_altitude_km,
                expected_altitude,
                rel_tol=0,
                abs_tol=1e-6,
            ):
                raise ValueError(
                    f"{row.subswath} {row.altitude_reference}-altitude angles "
                    "must match the source altitude range"
                )
        return self


class PointedReferenceCase(_StrictModel):
    """An exact, reproducible public-API integration case."""

    case_id: str = Field(min_length=1)
    fixture_case_id: str = Field(min_length=1)
    orbit_direction: Literal["ascending", "descending"]
    expected_event_count: Literal[0, 1]
    measured_epoch_residual_ms: Optional[StrictFloat] = Field(default=None, ge=0)
    measured_position_residual_m: Optional[StrictFloat] = Field(default=None, ge=0)
    epoch_tolerance_ms: StrictFloat = Field(gt=0)
    position_tolerance_m: StrictFloat = Field(gt=0)
    tolerance_basis: str = Field(min_length=1)
    evidence_ids: List[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_measurements(self) -> "PointedReferenceCase":
        if self.expected_event_count == 1:
            if self.measured_epoch_residual_ms is None or self.measured_position_residual_m is None:
                raise ValueError("included reference cases require measured residuals")
            if self.measured_epoch_residual_ms > self.epoch_tolerance_ms:
                raise ValueError("reference epoch residual exceeds its tolerance")
            if self.measured_position_residual_m > self.position_tolerance_m:
                raise ValueError("reference position residual exceeds its tolerance")
        elif (
            self.measured_epoch_residual_ms is not None
            or self.measured_position_residual_m is not None
        ):
            raise ValueError("non-event reference cases must not claim event residuals")
        return self


class MissionReferenceCheck(_StrictModel):
    """Measured mission-product context that does not claim access validation."""

    check_id: str = Field(min_length=1)
    source_evidence_id: str = Field(min_length=1)
    comparison_target: str = Field(min_length=1)
    sample_sizes: Dict[str, StrictInt] = Field(min_length=1)
    measured_residuals: Dict[str, StrictFloat] = Field(min_length=1)
    tolerance_limits: Dict[str, StrictFloat] = Field(min_length=1)
    tolerance_directions: Dict[str, Literal["at_most", "at_least"]] = Field(min_length=1)
    tolerance_basis: str = Field(min_length=1)
    status: Literal["reference_only"] = "reference_only"
    limitation: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_tolerances(self) -> "MissionReferenceCheck":
        if any(size <= 0 for size in self.sample_sizes.values()):
            raise ValueError("mission reference sample sizes must be positive")
        if set(self.measured_residuals) != set(self.tolerance_limits) or set(
            self.measured_residuals
        ) != set(self.tolerance_directions):
            raise ValueError(
                "mission reference measurements and per-measure tolerances must match"
            )
        for name, measured in self.measured_residuals.items():
            limit = self.tolerance_limits[name]
            if not math.isfinite(measured) or not math.isfinite(limit):
                raise ValueError("mission reference values and tolerances must be finite")
            direction = self.tolerance_directions[name]
            if direction == "at_most" and measured > limit:
                raise ValueError(f"mission reference measurement {name!r} exceeds tolerance")
            if direction == "at_least" and measured < limit:
                raise ValueError(f"mission reference measurement {name!r} is below tolerance")
        return self


class PointedGeometryCheck(_StrictModel):
    """One measured model-to-reference comparison and its interpretation."""

    check_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    method: str = Field(min_length=1)
    measured_value: StrictFloat
    unit: str = Field(min_length=1)
    tolerance: StrictFloat = Field(gt=0)
    tolerance_basis: str = Field(min_length=1)
    relation: Literal["at_most", "at_least"] = "at_most"
    status: Literal["passed", "limited"]
    evidence_ids: List[str] = Field(min_length=1)
    limitation: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_measured_value(self) -> "PointedGeometryCheck":
        if not math.isfinite(self.measured_value) or not math.isfinite(self.tolerance):
            raise ValueError("geometry-check measurements and tolerances must be finite")
        passes = (
            self.measured_value <= self.tolerance
            if self.relation == "at_most"
            else self.measured_value >= self.tolerance
        )
        if self.status == "passed" and not passes:
            raise ValueError(f"geometry check {self.check_id!r} exceeds its tolerance")
        return self


class PointedConfiguration(_StrictModel):
    """One complete, sourced pointed-instrument configuration."""

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    configuration_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    instrument: str = Field(min_length=1)
    spacecraft: SpacecraftIdentity
    operating_mode: Literal["IW"]
    channel: ChannelIdentity
    applicability: ApplicabilityInterval
    geometry: PointedGeometry
    evidence: List[SourceEvidence] = Field(min_length=1)
    source_revision: SourceRevision
    interpretation: Interpretation
    approximation_status: Literal["approximate", "limited"]
    approximations: List[Approximation] = Field(min_length=1)
    source_conflicts: List[SourceConflict] = Field(default_factory=list)
    reference_cases: List[PointedReferenceCase] = Field(min_length=4)
    mission_reference_checks: List[MissionReferenceCheck] = Field(min_length=1)
    geometry_checks: List[PointedGeometryCheck] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _reject_multiple_configurations(cls, data: Any) -> Any:
        if isinstance(data, (list, tuple)):
            raise ValueError(
                "exactly one pointed instrument configuration is supported in "
                "this increment; multiple configured instruments are not supported"
            )
        return data

    @model_validator(mode="after")
    def _check_evidence_and_limits(self) -> "PointedConfiguration":
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence contains duplicate evidence_id values")
        known = set(evidence_ids)
        supported: set[str] = set()
        for item in self.evidence:
            supported.update(item.supports)
        missing_support = sorted(REQUIRED_GEOMETRY_SUPPORT - supported)
        if missing_support:
            raise ValueError("missing source evidence for: " + ", ".join(missing_support))

        referenced = [
            *self.applicability.evidence_ids,
            *self.source_revision.evidence_ids,
            *self.geometry.geometry_evidence_ids,
        ]
        for row in self.geometry.source_subswath_angles:
            referenced.extend(row.evidence_ids)
        for item in self.approximations:
            referenced.extend(item.evidence_ids)
        for item in self.reference_cases:
            referenced.extend(item.evidence_ids)
        for item in self.geometry_checks:
            referenced.extend(item.evidence_ids)
        referenced.extend(item.source_evidence_id for item in self.mission_reference_checks)
        for conflict in self.source_conflicts:
            for option in conflict.options:
                referenced.extend(option.evidence_ids)
        missing = sorted(set(referenced) - known)
        if missing:
            raise ValueError(
                "configuration references unknown evidence_id values: "
                + ", ".join(missing)
            )

        if self.interpretation.selected_mode != self.operating_mode:
            raise ValueError("interpretation.selected_mode must match operating_mode 'IW'")
        if not any(
            case.expected_event_count == 1 and case.orbit_direction == "ascending"
            for case in self.reference_cases
        ) or not any(
            case.expected_event_count == 1 and case.orbit_direction == "descending"
            for case in self.reference_cases
        ):
            raise ValueError("reference cases must include both orbit directions")
        if not any(case.expected_event_count == 0 for case in self.reference_cases):
            raise ValueError("reference cases must include expected non-events")
        if not any(
            check.status == "reference_only"
            and "not validate" in check.limitation.lower()
            for check in self.mission_reference_checks
        ):
            raise ValueError(
                "mission product residuals must state that they do not validate "
                "the TAT-C access geometry"
            )
        return self

    def warnings(self) -> List[str]:
        """Return limitations that must accompany proposals and retrieved rows."""
        warnings = [
            "Modeled viewing epochs are access opportunities, not confirmation "
            f"that {self.spacecraft.name} acquired an IW scene at that place and time.",
            "The fixed rectangular view approximates altitude-dependent roll "
            "steering and the three TOPSAR IW subswaths; it does not model their "
            "switching, gaps, burst timing, or acquisition plan.",
            f"The published {self.spacecraft.name} TAT-C validation residuals are reference "
            "context only; they do not validate this exact TAT-C access mapping. "
            "Its along-track field differs from the product azimuth beam width "
            "used here.",
            "Events classify grid-cell centers only; they do not imply full-cell "
            "or full-region coverage.",
            "Configuration applicability is separate from orbit availability "
            "and confirmed acquisition schedules.",
            "The established applicability interval ends at "
            f"{iso_utc(self.applicability.end_utc_exclusive)}; later dates are "
            "not established for this configuration.",
        ]
        for item in self.approximations:
            warnings.append(f"Approximation {item.approximation_id}: {item.description}")
        for conflict in self.source_conflicts:
            warnings.append(
                f"Source conflict {conflict.conflict_id} resolved to "
                f"{conflict.chosen_option_id}: {conflict.rationale}"
            )
        return warnings

    def portable(self) -> Dict[str, Any]:
        """Return normalized, JSON-safe configuration data."""
        return self.model_dump(mode="json")


def configuration_from_snapshot(snapshot: Dict[str, Any]) -> PointedConfiguration:
    """Rebuild a pointed configuration from a resolved result snapshot."""
    data = {
        key: value
        for key, value in snapshot.items()
        if key not in ("tatc_mapping", "software")
    }
    try:
        return PointedConfiguration.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Saved pointed configuration is invalid: {exc}") from exc
