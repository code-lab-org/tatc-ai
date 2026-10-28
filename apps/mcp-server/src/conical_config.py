"""Typed, sourced configuration for a conical-scanning study.

The model keeps source meaning separate from the TAT-C mapping: every angle
carries its unit, every geometry parameter is backed by an evidence excerpt,
and source conflicts retain the option that was chosen and why.  Tool code
does not supply mission defaults; a researcher or calling assistant supplies
one complete configuration.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Sequence

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = "conical-configuration-v1"
REQUIRED_GEOMETRY_SUPPORT = (
    "geometry.cone_angle",
    "geometry.scan_center_azimuth",
    "geometry.scan_half_width",
    "geometry.along_track_field_of_view",
    "geometry.velocity_frame",
    "geometry.nadir_reference",
)
_ANGLE_UNITS = {
    "deg": "degree",
    "degree": "degree",
    "degrees": "degree",
    "°": "degree",
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("time must include a UTC offset")
    return value.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    """Return an ISO-8601 UTC string with a Z suffix."""
    return _utc(value).isoformat().replace("+00:00", "Z")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Angle(_StrictModel):
    """A finite angle with an explicit unit."""

    value: float
    unit: str

    @field_validator("unit")
    @classmethod
    def _check_unit(cls, value: str) -> str:
        normalized = _ANGLE_UNITS.get(value.strip().lower())
        if normalized is None:
            raise ValueError(
                "angle unit must be degrees (accepted: degree, degrees, deg, °), "
                f"got {value!r}"
            )
        return normalized

    @field_validator("value", mode="before")
    @classmethod
    def _reject_bool(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("angle value must be a number, not a boolean")
        return value

    @field_validator("value")
    @classmethod
    def _check_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("angle value must be finite")
        return value


class SpacecraftIdentity(_StrictModel):
    """Spacecraft identity and its orbit-provider lookup."""

    name: str = Field(min_length=1)
    aliases: List[str] = Field(default_factory=list)
    norad_id: int = Field(gt=0)
    orbit_provider: Literal["celestrak"] = "celestrak"
    orbit_provider_identifier: str = Field(min_length=1)


class ChannelIdentity(_StrictModel):
    """Channel or band whose geometry the configuration describes."""

    name: str = Field(min_length=1)
    center_frequency_ghz: float = Field(gt=0)

    @field_validator("center_frequency_ghz", mode="before")
    @classmethod
    def _reject_bool(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("center_frequency_ghz must be a number, not a boolean")
        return value
    polarization: Optional[str] = None
    notes: Optional[str] = None


class ApplicabilityInterval(_StrictModel):
    """Established UTC interval in which the configuration applies."""

    start_utc: AwareDatetime
    end_utc_exclusive: AwareDatetime
    evidence_ids: List[str] = Field(min_length=1)
    basis: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_order(self) -> "ApplicabilityInterval":
        if _utc(self.start_utc) >= _utc(self.end_utc_exclusive):
            raise ValueError("applicability start_utc must precede end_utc_exclusive")
        return self


class SourceEvidence(_StrictModel):
    """One sourced fact, excerpt, and the parameters it supports."""

    evidence_id: str = Field(min_length=1)
    authority: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    document_revision: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    retrieved_at_utc: AwareDatetime
    interpretation: str = Field(min_length=1)
    supports: List[str] = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("evidence url must be an http(s) URL")
        return value


class SourceRevision(_StrictModel):
    """Revision of the source set checked for this configuration."""

    revision_id: str = Field(min_length=1)
    checked_at_utc: AwareDatetime
    authority: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    evidence_ids: List[str] = Field(min_length=1)


class Interpretation(_StrictModel):
    """The assistant's reading of the sources and its stated limits."""

    summary: str = Field(min_length=1)
    selected_mode: str = Field(min_length=1)
    limitations: List[str] = Field(min_length=1)


class Approximation(_StrictModel):
    """A declared simplification that remains attached to the result."""

    approximation_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    affected_parameters: List[str] = Field(min_length=1)
    impact: str = Field(min_length=1)
    evidence_ids: List[str] = Field(min_length=1)


class ConflictOption(_StrictModel):
    """One documented option in a source conflict.

    Optional numeric fields let the server check that the selected option is
    the one the geometry actually uses, rather than a label pasted beside
    another sector's widths.
    """

    option_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    evidence_ids: List[str] = Field(min_length=1)
    cone_angle_deg: Optional[float] = None
    scan_center_azimuth_deg: Optional[float] = None
    scan_half_width_deg: Optional[float] = None
    along_track_field_of_view_deg: Optional[float] = None
    velocity_frame: Optional[Literal["inertial", "earth_fixed"]] = None
    incidence_angle_deg: Optional[float] = None
    swath_width_km: Optional[float] = None


class SourceConflict(_StrictModel):
    """A source disagreement and the researcher's selected option."""

    conflict_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    options: List[ConflictOption] = Field(min_length=2)
    chosen_option_id: Optional[str] = None
    rationale: Optional[str] = None

    @model_validator(mode="after")
    def _check_choice(self) -> "SourceConflict":
        ids = [option.option_id for option in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError(f"source conflict {self.conflict_id!r} repeats an option_id")
        if self.chosen_option_id is None:
            raise ValueError(
                f"source conflict {self.conflict_id!r} is unresolved; record "
                "chosen_option_id and rationale before calculation"
            )
        if self.chosen_option_id not in ids:
            raise ValueError(
                f"source conflict {self.conflict_id!r} chooses unknown option "
                f"{self.chosen_option_id!r}"
            )
        if self.rationale is None or not self.rationale.strip():
            raise ValueError(
                f"source conflict {self.conflict_id!r} needs a rationale for its choice"
            )
        return self

    @property
    def chosen(self) -> ConflictOption:
        return next(
            option for option in self.options if option.option_id == self.chosen_option_id
        )


class GeometryCheck(_StrictModel):
    """A reported geometry or mission-reference check, with its scope."""

    check_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    method: str = Field(min_length=1)
    reported_value: Optional[float] = None
    unit: Optional[str] = None
    tolerance: Optional[float] = None
    status: Literal["passed", "limited", "failed"]
    evidence_ids: List[str] = Field(min_length=1)


class ConicalGeometry(_StrictModel):
    """Typed conical scan geometry in degrees and stated reference frames."""

    model_family: str
    cone_angle: Angle
    scan_center_azimuth: Angle
    scan_half_width: Angle
    along_track_field_of_view: Angle
    velocity_frame: Literal["inertial", "earth_fixed"]
    nadir_reference: Literal["geodetic", "geocentric"]
    illumination_requirement: Literal["none", "target_sunlit"]
    beam_width: Optional[Angle] = None
    scan_sample_spacing: Optional[Angle] = None

    @model_validator(mode="after")
    def _check_values(self) -> "ConicalGeometry":
        if self.model_family != "conical":
            raise ValueError(
                "unsupported geometry model family "
                f"{self.model_family!r}; this increment supports only 'conical'"
            )
        if not 0 < self.cone_angle.value < 90:
            raise ValueError("cone_angle must be greater than 0 and less than 90 degrees")
        if not -180 <= self.scan_center_azimuth.value <= 180:
            raise ValueError("scan_center_azimuth must be within [-180, 180] degrees")
        if not 0 < self.scan_half_width.value <= 180:
            raise ValueError("scan_half_width must be greater than 0 and at most 180 degrees")
        if not 0 < self.along_track_field_of_view.value < 180:
            raise ValueError(
                "along_track_field_of_view must be greater than 0 and less than "
                "180 degrees"
            )
        if self.beam_width is not None and not 0 < self.beam_width.value < 90:
            raise ValueError("beam_width must be greater than 0 and less than 90 degrees")
        if self.scan_sample_spacing is not None and not (
            0 < self.scan_sample_spacing.value <= 180
        ):
            raise ValueError("scan_sample_spacing must be greater than 0 and at most 180 degrees")
        # TAT-C's ConicalInstrument derives its field of regard from these
        # values and caps it at 180 degrees. Reject a combination that would
        # be silently clipped.
        derived_for = 2 * (
            self.cone_angle.value + self.along_track_field_of_view.value / 2 + 1
        )
        if derived_for > 180:
            raise ValueError(
                "cone_angle and along_track_field_of_view require a TAT-C field "
                "of regard beyond 180 degrees; reduce one of them"
            )
        return self


class ConicalConfiguration(_StrictModel):
    """One complete, sourced, study-specific conical configuration."""

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    configuration_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    instrument: str = Field(min_length=1)
    spacecraft: SpacecraftIdentity
    operating_mode: str = Field(min_length=1)
    channel: ChannelIdentity
    applicability: ApplicabilityInterval
    geometry: ConicalGeometry
    evidence: List[SourceEvidence] = Field(min_length=1)
    source_revision: SourceRevision
    interpretation: Interpretation
    approximation_status: Literal["supported", "approximate", "limited"]
    approximations: List[Approximation] = Field(default_factory=list)
    source_conflicts: List[SourceConflict] = Field(default_factory=list)
    geometry_checks: List[GeometryCheck] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _reject_lists(cls, data: Any) -> Any:
        if isinstance(data, (list, tuple)):
            raise ValueError(
                "exactly one conical configuration is supported in this "
                "increment; multi-instrument and multi-configuration studies "
                "are not yet supported"
            )
        return data

    @model_validator(mode="after")
    def _check_references(self) -> "ConicalConfiguration":
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence contains duplicate evidence_id values")
        known = set(evidence_ids)

        referenced: List[str] = list(self.applicability.evidence_ids)
        referenced += list(self.source_revision.evidence_ids)
        for collection in (
            self.approximations,
            self.geometry_checks,
        ):
            for item in collection:
                referenced += list(item.evidence_ids)
        for conflict in self.source_conflicts:
            for option in conflict.options:
                referenced += list(option.evidence_ids)
        missing = sorted(set(referenced) - known)
        if missing:
            raise ValueError(
                "configuration references unknown evidence_id values: "
                + ", ".join(missing)
            )

        supported: set[str] = set()
        for item in self.evidence:
            supported.update(item.supports)
        missing_support = sorted(set(REQUIRED_GEOMETRY_SUPPORT) - supported)
        if self.geometry.beam_width is not None:
            missing_support += (
                ["geometry.beam_width"]
                if "geometry.beam_width" not in supported
                else []
            )
        if self.geometry.scan_sample_spacing is not None:
            missing_support += (
                ["geometry.scan_sample_spacing"]
                if "geometry.scan_sample_spacing" not in supported
                else []
            )
        if missing_support:
            raise ValueError(
                "missing source evidence for: " + ", ".join(missing_support)
            )

        if self.approximations and self.approximation_status == "supported":
            raise ValueError(
                "a configuration with declared approximations must use "
                "approximation_status 'approximate' or 'limited', not 'supported'"
            )
        if self.approximation_status == "approximate" and not self.approximations:
            raise ValueError(
                "approximation_status 'approximate' requires at least one "
                "documented approximation"
            )
        if self.interpretation.selected_mode != self.operating_mode:
            raise ValueError(
                "interpretation.selected_mode must match operating_mode "
                f"({self.interpretation.selected_mode!r} != {self.operating_mode!r})"
            )
        if not self.geometry_checks:
            raise ValueError(
                "at least one reported geometry check is required; a citation "
                "or interpretation alone does not establish a mapping check"
            )
        if any(check.status == "failed" for check in self.geometry_checks):
            raise ValueError(
                "a reported geometry check failed; the configuration cannot be "
                "accepted for calculation"
            )

        for conflict in self.source_conflicts:
            choice = conflict.chosen
            checks = (
                ("cone_angle_deg", self.geometry.cone_angle.value),
                ("scan_center_azimuth_deg", self.geometry.scan_center_azimuth.value),
                ("scan_half_width_deg", self.geometry.scan_half_width.value),
                (
                    "along_track_field_of_view_deg",
                    self.geometry.along_track_field_of_view.value,
                ),
            )
            for field, actual in checks:
                expected = getattr(choice, field)
                if expected is not None and not math.isclose(
                    float(expected), float(actual), rel_tol=0, abs_tol=1e-6
                ):
                    raise ValueError(
                        f"source conflict {conflict.conflict_id!r} chose "
                        f"{choice.option_id!r} with {field}={expected}, but the "
                        f"geometry uses {actual}; do not combine values from "
                        "different options"
                    )
            if (
                choice.velocity_frame is not None
                and choice.velocity_frame != self.geometry.velocity_frame
            ):
                raise ValueError(
                    f"source conflict {conflict.conflict_id!r} chose velocity "
                    f"frame {choice.velocity_frame!r}, but the geometry uses "
                    f"{self.geometry.velocity_frame!r}"
                )
        return self

    def warnings(self) -> List[str]:
        """Return warnings that must stay with proposals and fetch responses."""
        warnings = [
            "Modeled viewing epochs are not confirmation of an actual "
            "instrument acquisition or usable data product.",
            "Events are sampled at grid-cell centers; an observed cell does not "
            "mean every point in the cell or region was viewed.",
            "The narrower JAXA nominal performance-guarantee scan sector is a "
            "different option; this configuration uses the wider product sector."
            if any(
                "nominal" in conflict.description.lower()
                for conflict in self.source_conflicts
            )
            else "",
            "The established applicability interval ends at "
            f"{iso_utc(self.applicability.end_utc_exclusive)}; dates after that "
            "are not established for this configuration.",
        ]
        for item in self.approximations:
            warnings.append(f"Approximation {item.approximation_id}: {item.description}")
        for conflict in self.source_conflicts:
            warnings.append(
                f"Source conflict {conflict.conflict_id} resolved to "
                f"{conflict.chosen_option_id}: {conflict.rationale}"
            )
        return [warning for warning in warnings if warning]

    def portable(self) -> Dict[str, Any]:
        """Return the normalized JSON-safe configuration."""
        return self.model_dump(mode="json")

    def source_choices(self) -> List[Dict[str, str]]:
        return [
            {
                "conflict_id": conflict.conflict_id,
                "chosen_option_id": str(conflict.chosen_option_id),
                "rationale": str(conflict.rationale),
            }
            for conflict in self.source_conflicts
        ]


def configuration_from_snapshot(snapshot: Dict[str, Any]) -> ConicalConfiguration:
    """Rebuild a configuration from a stored resolved snapshot.

    The snapshot adds TAT-C-derived fields that are not part of the configuration
    input; drop those before revalidation.
    """
    data = {
        key: value
        for key, value in snapshot.items()
        if key not in ("tatc_mapping", "software")
    }
    return ConicalConfiguration.model_validate(data)
