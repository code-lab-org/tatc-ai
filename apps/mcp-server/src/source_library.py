"""Read reviewed source documents and resolve configuration citations.

A source document is one immutable record of a source at one revision. It
carries the document metadata once and the excerpts taken from it. A
configuration cites an excerpt by document ID, revision, and excerpt ID, and
keeps its own interpretation of what that excerpt supports.

Resolution happens before configuration validation, so the rest of the server
continues to consume the existing evidence shape.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = "source-library-v1"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SOURCES_DIR = DATA_DIR / "sources"

_REFERENCE = re.compile(
    r"^(?P<document_id>[^@#]+)@(?P<revision>[^@#]+)#(?P<excerpt_id>[^@#]+)$"
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceExcerpt(_StrictModel):
    """One short passage copied from a document, with a locator."""

    excerpt_id: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    text: str = Field(min_length=1)


class SourceDocument(_StrictModel):
    """One document at one revision, with the excerpts taken from it."""

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    document_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    authority: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    document_revision: str = Field(min_length=1)
    retrieved_at_utc: AwareDatetime
    excerpts: List[SourceExcerpt] = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("document url must be an http(s) URL")
        return value

    @model_validator(mode="after")
    def _check_excerpt_ids(self) -> "SourceDocument":
        ids = [item.excerpt_id for item in self.excerpts]
        if len(ids) != len(set(ids)):
            raise ValueError(
                f"document {self.document_id!r} revision {self.revision!r} "
                "repeats an excerpt_id"
            )
        return self


class SourceClaim(_StrictModel):
    """A configuration's use of one excerpt."""

    evidence_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    interpretation: str = Field(min_length=1)
    supports: List[str] = Field(default_factory=list)

    @field_validator("source")
    @classmethod
    def _check_reference(cls, value: str) -> str:
        if not _REFERENCE.match(value):
            raise ValueError(
                "source must be 'document_id@revision#excerpt_id', "
                f"got {value!r}"
            )
        return value


@dataclass(frozen=True)
class SourceLibrary:
    """Documents keyed by document ID and revision."""

    documents: Dict[Tuple[str, str], SourceDocument]

    def resolve(self, claim: SourceClaim) -> Dict[str, Any]:
        match = _REFERENCE.match(claim.source)
        if match is None:
            raise ValueError(f"malformed source reference {claim.source!r}")
        key = (match.group("document_id"), match.group("revision"))
        document = self.documents.get(key)
        if document is None:
            raise ValueError(
                f"no document revision {key[0]!r}@{key[1]!r} exists in the source library"
            )
        excerpt_id = match.group("excerpt_id")
        excerpt = next(
            (item for item in document.excerpts if item.excerpt_id == excerpt_id),
            None,
        )
        if excerpt is None:
            raise ValueError(
                f"document {key[0]!r} revision {key[1]!r} has no excerpt "
                f"{excerpt_id!r}"
            )
        return {
            "evidence_id": claim.evidence_id,
            "authority": document.authority,
            "title": document.title,
            "url": document.url,
            "document_revision": document.document_revision,
            "locator": excerpt.locator,
            "excerpt": excerpt.text,
            "retrieved_at_utc": document.retrieved_at_utc,
            "interpretation": claim.interpretation,
            "supports": list(claim.supports),
        }


def load_source_library(root: Optional[Path] = None) -> SourceLibrary:
    """Read every document revision under the source library root."""
    source_root = SOURCES_DIR if root is None else Path(root)
    documents: Dict[Tuple[str, str], SourceDocument] = {}
    if not source_root.exists():
        return SourceLibrary(documents=documents)
    for path in sorted(source_root.rglob("*.json")):
        try:
            document = SourceDocument.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise ValueError(f"Invalid source document {path}: {exc}") from exc
        key = (document.document_id, document.revision)
        if key in documents:
            raise ValueError(
                "Source library repeats document "
                f"{key[0]!r} revision {key[1]!r}"
            )
        documents[key] = document
    return SourceLibrary(documents=documents)


def resolve_configuration_data(
    data: Any,
    *,
    root: Optional[Path] = None,
) -> Any:
    """Replace a configuration's claims with resolved evidence records.

    A record carries either inline evidence or claims, never both. Data
    without claims is returned unchanged so the configuration model can
    validate it.
    """
    if not isinstance(data, dict):
        return data
    claims = data.get("source_claims")
    if claims is None:
        return data
    if "evidence" in data:
        identifier = data.get("configuration_id") or "<unknown>"
        raise ValueError(
            f"Configuration {identifier!r} carries both inline evidence and "
            "source_claims; keep exactly one form"
        )
    if not isinstance(claims, list) or not claims:
        identifier = data.get("configuration_id") or "<unknown>"
        raise ValueError(
            f"Configuration {identifier!r} has no usable source_claims; "
            "supply at least one claim or use inline evidence"
        )
    identifier = data.get("configuration_id") or "<unknown>"
    revision = data.get("revision") or "<unknown>"
    library = load_source_library(root)
    resolved: List[Dict[str, Any]] = []
    for raw_claim in claims:
        try:
            claim = SourceClaim.model_validate(raw_claim)
        except ValidationError as exc:
            raise ValueError(
                f"Configuration {identifier!r} revision {revision!r} has an "
                f"invalid source claim: {exc}"
            ) from exc
        try:
            resolved.append(library.resolve(claim))
        except ValueError as exc:
            raise ValueError(
                f"Configuration {identifier!r} revision {revision!r} claim "
                f"{claim.evidence_id!r} cannot resolve {claim.source!r}: {exc}"
            ) from exc
    resolved_data = dict(data)
    resolved_data.pop("source_claims")
    resolved_data["evidence"] = resolved
    return resolved_data
