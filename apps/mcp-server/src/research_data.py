"""Typed numerical data for research charts, independent of Cesium or propagation."""

from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

# List sizes, kind and provenance are checked in matched_series rather than the
# JSON schema, which LibreChat reports to the model only as a schema mismatch.


class ResearchSeries(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=80)
    values: list[float | None]


class DataProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["user", "study"]
    analysis_id: Optional[str] = Field(
        default=None, description="The coverage study's analysis_id, for kind='study'."
    )


class ResearchData(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    title: str = Field(min_length=1, max_length=120)
    labels: list[str] = Field(description="1–2000 labels.")
    series: list[ResearchSeries] = Field(description="1–8 series, one value per label.")
    kind: Optional[Literal["bar", "line"]] = Field(
        default=None,
        description="Required. 'line' for a line chart or run-ordered series, 'bar' for category comparisons.",
    )
    provenance: Optional[DataProvenance] = Field(
        default=None,
        description="Required. {'kind': 'user'} only when the user typed every value in "
        "this conversation; {'kind': 'study', 'analysis_id': ...} for metrics of a coverage "
        "study. Never chart invented, estimated or example values.",
    )
    x_label: str = Field(default="", max_length=80)
    unit: str = Field(default="", max_length=40)
    source: str = Field(
        default="",
        max_length=500,
        description="Optional source attribution, when known (for example a dataset or earlier analysis). Omit for values supplied directly in the prompt. Do not invent a citation or use the chart title as a source.",
    )

    @model_validator(mode="after")
    def matched_series(self):
        if self.kind is None:
            raise ValueError(
                "kind is required: 'line' for a line chart or run-ordered series, "
                "'bar' for category comparisons"
            )
        if self.provenance is None or (
            self.provenance.kind == "study" and not self.provenance.analysis_id
        ):
            raise ValueError(
                "provenance is required: {'kind': 'user'} only when the user typed "
                "every value, or {'kind': 'study', 'analysis_id': ...} for values from "
                "a coverage study result. Do not chart invented values; ask the user."
            )
        if not 1 <= len(self.labels) <= 2000 or not 1 <= len(self.series) <= 8:
            raise ValueError("A dataset needs 1–2000 labels and 1–8 series")
        if any(len(s.values) != len(self.labels) for s in self.series):
            raise ValueError(
                "Each series must have one value per label; use null for missing values"
            )
        if len({s.name for s in self.series}) != len(self.series):
            raise ValueError("Series names must be unique")
        if any(len(label) > 120 for label in self.labels):
            raise ValueError("Chart labels must be at most 120 characters")
        if len(self.labels) * len(self.series) > 6000:
            raise ValueError("Research data exceeds 6000 values")
        return self
