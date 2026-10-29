"""Lookup reviewed instrument configuration revisions stored with the server."""

from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Optional, Tuple

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .conical_config import ConicalConfiguration, SourceEvidence

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
REGISTRY_PATH = DATA_DIR / "instrument_configuration_registry.json"
MAX_REFRESH_RECORDS = 256
MAX_REFRESH_CANDIDATES = 32
MAX_REFRESH_STATE_BYTES = 1_048_576
NOTICE_STATUSES = (
    "documentation_changed",
    "geometry_changed",
    "applicability_changed",
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceChangeCheck(_StrictModel):
    """A reported source check attached to one immutable configuration revision."""

    check_id: str = Field(min_length=1)
    kind: Literal["initial_inclusion", "explicit_refresh"]
    status: Literal[
        "checked",
        "unchanged",
        "documentation_changed",
        "geometry_changed",
        "applicability_changed",
        "failed",
    ]
    checked_at_utc: AwareDatetime
    checked_source_revision_id: str = Field(min_length=1)
    reported_by: Literal["assistant", "maintainer"]
    summary: str = Field(min_length=1)
    evidence_ids: List[str] = Field(default_factory=list)
    source_evidence: List[SourceEvidence] = Field(default_factory=list)
    failure_reason: Optional[str] = None
    new_revision: Optional[str] = None
    geometry_changes: List[Dict[str, Any]] = Field(default_factory=list)
    applicability_changes: List[Dict[str, Any]] = Field(default_factory=list)

    @field_validator("checked_at_utc")
    @classmethod
    def _normalize_checked_at_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _check_status_fields(self) -> "SourceChangeCheck":
        if self.kind == "initial_inclusion" and self.status != "checked":
            raise ValueError("initial inclusion checks must use status 'checked'")
        if self.kind == "explicit_refresh" and self.status == "checked":
            raise ValueError("explicit refresh checks must record their finding")
        if self.status == "failed" and not (self.failure_reason or "").strip():
            raise ValueError("failed source checks require a failure_reason")
        if self.status in ("unchanged", "documentation_changed") and not self.evidence_ids:
            raise ValueError(f"{self.status} source checks require evidence_ids")
        if self.status == "geometry_changed":
            if not self.evidence_ids or not self.new_revision or not self.geometry_changes:
                raise ValueError(
                    "geometry_changed checks require evidence_ids, new_revision, "
                    "and geometry_changes"
                )
        if self.status == "applicability_changed":
            if (
                not self.evidence_ids
                or not self.new_revision
                or not self.applicability_changes
            ):
                raise ValueError(
                    "applicability_changed checks require evidence_ids, "
                    "new_revision, and applicability_changes"
                )
            if self.geometry_changes:
                raise ValueError(
                    "applicability_changed checks cannot include geometry_changes; "
                    "use geometry_changed when geometry also changes"
                )
        return self


class RegistryEntry(_StrictModel):
    """A configuration file and its review/source-check records."""

    configuration_path: str = Field(min_length=1)
    registry_status: Literal["reviewed_shared"]
    source_change_checks: List[SourceChangeCheck] = Field(min_length=1)


@dataclass
class _RefreshState:
    lock: Any = field(default_factory=threading.RLock)
    checks: Dict[Tuple[str, str], List[Dict[str, Any]]] = field(default_factory=dict)
    candidates: List[Tuple[ConicalConfiguration, Dict[str, Any]]] = field(
        default_factory=list
    )


_default_refresh_state = _RefreshState()
_refresh_state_context: ContextVar[Optional[_RefreshState]] = ContextVar(
    "instrument_configuration_refresh_state", default=None
)


def _current_refresh_state() -> _RefreshState:
    return _refresh_state_context.get() or _default_refresh_state


@contextmanager
def isolated_refresh_state() -> Iterator[None]:
    """Give one conversation isolated refresh state across async tool calls."""
    token = _refresh_state_context.set(_RefreshState())
    try:
        yield
    finally:
        _refresh_state_context.reset(token)


def _canonical_payload_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def _serialized_refresh_state_bytes(
    state: _RefreshState,
    extra_record: Optional[Tuple[Tuple[str, str], Dict[str, Any]]] = None,
    extra_candidate: Optional[
        Tuple[ConicalConfiguration, Dict[str, Any]]
    ] = None,
) -> int:
    check_groups = {
        key: [dict(record) for record in records]
        for key, records in state.checks.items()
    }
    checks = [
        {
            "configuration_id": key[0],
            "base_revision": key[1],
            "records": records,
        }
        for key, records in sorted(check_groups.items())
    ]
    candidates = [
        {"configuration": configuration.portable(), "metadata": metadata}
        for configuration, metadata in state.candidates
    ]
    if extra_record is not None:
        key, record = extra_record
        group = next(
            (item for item in checks if (item["configuration_id"], item["base_revision"]) == key),
            None,
        )
        if group is None:
            checks.append(
                {
                    "configuration_id": key[0],
                    "base_revision": key[1],
                    "records": [record],
                }
            )
        else:
            group["records"].append(record)
    if extra_candidate is not None:
        configuration, metadata = extra_candidate
        candidates.append(
            {"configuration": configuration.portable(), "metadata": metadata}
        )
    return len(_canonical_payload_bytes({"checks": checks, "candidates": candidates}))


def _load_entries() -> List[Tuple[ConicalConfiguration, Dict[str, Any]]]:
    try:
        manifest = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read the instrument configuration registry: {exc}") from exc
    if manifest.get("schema_version") != "instrument-configuration-registry-v1":
        raise ValueError("Unsupported instrument configuration registry schema_version")

    entries: List[Tuple[ConicalConfiguration, Dict[str, Any]]] = []
    seen: set[Tuple[str, str]] = set()
    for raw_entry in manifest.get("entries", []):
        try:
            entry = RegistryEntry.model_validate(raw_entry)
        except ValidationError as exc:
            raise ValueError(f"Invalid registry entry: {exc}") from exc
        config_path = (DATA_DIR / entry.configuration_path).resolve()
        if not config_path.is_relative_to(DATA_DIR.resolve()):
            raise ValueError("Registry configuration_path must remain inside the data directory")
        try:
            configuration = ConicalConfiguration.model_validate_json(
                config_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise ValueError(
                f"Invalid configuration in {entry.configuration_path}: {exc}"
            ) from exc
        key = (configuration.configuration_id, configuration.revision)
        if key in seen:
            raise ValueError(
                "Registry configuration IDs and revisions must be unique; "
                f"duplicate {key[0]!r} revision {key[1]!r}"
            )
        seen.add(key)
        evidence_ids = {item.evidence_id for item in configuration.evidence}
        for check in entry.source_change_checks:
            if (
                check.kind == "initial_inclusion"
                and check.checked_source_revision_id
                != configuration.source_revision.revision_id
            ):
                raise ValueError(
                    f"Source check {check.check_id!r} does not match the configuration's "
                    "source_revision"
                )
            missing_evidence = set(check.evidence_ids) - evidence_ids
            if missing_evidence:
                raise ValueError(
                    f"Source check {check.check_id!r} references missing evidence: "
                    + ", ".join(sorted(missing_evidence))
                )
        entries.append(
            (
                configuration,
                {
                    "registry_status": entry.registry_status,
                    "source_change_checks": [
                        check.model_dump(mode="json")
                        for check in entry.source_change_checks
                    ],
                },
            )
        )
    return entries


def _current_source_state(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Separate the latest source status from retained earlier checks."""
    refreshes = [
        (index, check)
        for index, check in enumerate(checks)
        if check["kind"] == "explicit_refresh"
    ]
    if not refreshes:
        return {
            "current_source_status": "checked",
            "source_change_notices": [],
            "refresh_uncertainty": [],
            "superseded_refresh_failures": [],
        }
    _, latest = max(
        refreshes, key=lambda item: (item[1]["checked_at_utc"], item[0])
    )
    return {
        "current_source_status": latest["status"],
        "source_change_notices": [
            check for check in checks if check["status"] in NOTICE_STATUSES
        ],
        "refresh_uncertainty": [latest] if latest["status"] == "failed" else [],
        "superseded_refresh_failures": [
            check
            for _, check in refreshes
            if check["status"] == "failed" and check["check_id"] != latest["check_id"]
        ],
    }


def _entries_with_refreshes() -> List[Tuple[ConicalConfiguration, Dict[str, Any]]]:
    entries = _load_entries()
    state = _current_refresh_state()
    with state.lock:
        checks_by_key = {
            key: [dict(check) for check in checks]
            for key, checks in state.checks.items()
        }
        candidates = list(state.candidates)

    result = []
    for configuration, original_metadata in entries:
        key = (configuration.configuration_id, configuration.revision)
        metadata = dict(original_metadata)
        refreshes = checks_by_key.get(key, [])
        checks = metadata["source_change_checks"] + refreshes
        metadata["source_change_checks"] = checks
        metadata.update(_current_source_state(checks))
        result.append((configuration, metadata))
    base_metadata_by_key = {
        (configuration.configuration_id, configuration.revision): metadata
        for configuration, metadata in result
    }
    for configuration, original_metadata in candidates:
        metadata = dict(original_metadata)
        base_key = (configuration.configuration_id, metadata["base_revision"])
        base_checks = base_metadata_by_key.get(base_key, {}).get(
            "source_change_checks", []
        )
        checks = []
        seen_check_ids = set()
        for check in [*base_checks, *metadata["source_change_checks"]]:
            if check["check_id"] not in seen_check_ids:
                checks.append(check)
                seen_check_ids.add(check["check_id"])
        metadata["source_change_checks"] = checks
        metadata.update(_current_source_state(checks))
        result.append((configuration, metadata))
    return result


def clear_refresh_state() -> None:
    """Clear refresh records in the active conversation or default state."""
    state = _current_refresh_state()
    with state.lock:
        state.checks.clear()
        state.candidates.clear()


def _geometry_changes(
    before: ConicalConfiguration, after: ConicalConfiguration
) -> List[Dict[str, Any]]:
    old = before.geometry.model_dump(mode="json")
    new = after.geometry.model_dump(mode="json")
    return [
        {
            "parameter": f"geometry.{name}",
            "previous_value": old[name],
            "new_value": new[name],
        }
        for name in old
        if old[name] != new[name]
    ]


def _applicability_changes(
    before: ConicalConfiguration, after: ConicalConfiguration
) -> List[Dict[str, Any]]:
    old = before.applicability.model_dump(mode="json")
    new = after.applicability.model_dump(mode="json")
    return [
        {
            "parameter": f"applicability.{name}",
            "previous_value": old[name],
            "new_value": new[name],
        }
        for name in old
        if old[name] != new[name]
    ]


def _validate_applicability_refresh(
    changes: List[Dict[str, Any]],
    source_evidence: List[SourceEvidence],
    updated_configuration: ConicalConfiguration,
) -> None:
    changed_dates = {
        change["parameter"]
        for change in changes
        if change["parameter"]
        in ("applicability.start_utc", "applicability.end_utc_exclusive")
    }
    required_support = set()
    if "applicability.start_utc" in changed_dates:
        required_support.add("applicability.start")
    if "applicability.end_utc_exclusive" in changed_dates:
        required_support.add("applicability.end")
    if not required_support:
        return
    supported = {
        parameter
        for item in source_evidence
        for parameter in item.supports
    }
    missing = sorted(required_support - supported)
    if missing:
        raise ValueError(
            "Refresh evidence must support changed applicability: "
            + ", ".join(missing)
        )
    evidence_ids = {
        item.evidence_id
        for item in source_evidence
        if required_support.intersection(item.supports)
    }
    if not evidence_ids.intersection(updated_configuration.applicability.evidence_ids):
        raise ValueError(
            "Updated configuration must retain the supplied evidence for changed "
            "applicability"
        )


def _validate_revised_assessment(
    base: ConicalConfiguration,
    updated: ConicalConfiguration,
    changes: List[Dict[str, Any]],
    source_evidence: List[SourceEvidence],
) -> None:
    """Reject stale checks and silent removal of declared simplifications."""
    numeric_changes = [
        (
            change["parameter"],
            float(change["previous_value"]["value"]),
            float(change["new_value"]["value"]),
            change["previous_value"].get("unit"),
        )
        for change in changes
        if isinstance(change["previous_value"], dict)
        and "value" in change["previous_value"]
    ]
    stale_checks = []
    for check in updated.geometry_checks:
        if check.reported_value is None:
            continue
        for parameter, previous, current, unit in numeric_changes:
            if check.unit != unit:
                continue
            tolerance = max(float(check.tolerance or 0.0), 1e-9)
            if (
                abs(check.reported_value - previous) <= tolerance
                and abs(check.reported_value - current) > tolerance
            ):
                stale_checks.append((check.check_id, parameter))
                break
    if stale_checks:
        raise ValueError(
            "Changed geometry leaves stale geometry checks reporting the prior "
            "value: "
            + ", ".join(f"{check_id} ({parameter})" for check_id, parameter in stale_checks)
        )

    if changes:
        refresh_evidence_ids = {item.evidence_id for item in source_evidence}
        if not any(
            refresh_evidence_ids.intersection(check.evidence_ids)
            for check in updated.geometry_checks
        ):
            raise ValueError(
                "A changed-geometry revision must include a geometry check that "
                "cites the refresh evidence"
            )

    updated_approximations = {
        item.approximation_id: item for item in updated.approximations
    }
    retained_evidence = {
        item.evidence_id: item.model_dump(mode="json")
        for item in updated.evidence
    }
    removed = []
    narrowed = []
    for approximation in base.approximations:
        replacement = updated_approximations.get(approximation.approximation_id)
        if replacement is None:
            token = f"approximation.{approximation.approximation_id}.removed"
            supporting = [
                item
                for item in source_evidence
                if token in item.supports
                and retained_evidence.get(item.evidence_id)
                == item.model_dump(mode="json")
            ]
            if not supporting:
                removed.append(approximation.approximation_id)
            continue
        missing_parameters = sorted(
            set(approximation.affected_parameters)
            - set(replacement.affected_parameters)
        )
        if missing_parameters:
            narrowed.append(
                f"{approximation.approximation_id} ({', '.join(missing_parameters)})"
            )
    if removed:
        raise ValueError(
            "A revised configuration removes declared approximations without "
            "evidence supporting approximation.<id>.removed: "
            + ", ".join(sorted(removed))
        )
    if narrowed:
        raise ValueError(
            "A revised configuration narrows declared approximation scope: "
            + ", ".join(narrowed)
        )


def record_instrument_configuration_refresh(
    configuration_id: str,
    base_revision: str,
    status: Literal[
        "unchanged",
        "documentation_changed",
        "geometry_changed",
        "applicability_changed",
        "failed",
    ],
    checked_at_utc: AwareDatetime,
    checked_source_revision_id: str,
    reported_by: Literal["assistant", "maintainer"],
    summary: str,
    evidence_ids: Optional[List[str]] = None,
    source_evidence: Optional[List[SourceEvidence]] = None,
    updated_configuration: Optional[ConicalConfiguration] = None,
    failure_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Record an explicit source check without changing a saved revision."""
    key = (configuration_id.strip(), base_revision.strip())
    if not all(key):
        raise ValueError("configuration_id and base_revision must be non-empty")
    base_entry = next(
        (
            (configuration, metadata)
            for configuration, metadata in _entries_with_refreshes()
            if (configuration.configuration_id, configuration.revision) == key
        ),
        None,
    )
    if base_entry is None:
        raise ValueError(
            f"No registry revision {key[1]!r} exists for configuration {key[0]!r}"
        )
    base_configuration, base_metadata = base_entry
    checked_at_utc = checked_at_utc.astimezone(timezone.utc)
    if base_metadata["registry_status"] != "reviewed_shared":
        raise ValueError("A source refresh must start from a reviewed shared revision")

    if isinstance(updated_configuration, dict):
        try:
            updated_configuration = ConicalConfiguration.model_validate(
                updated_configuration
            )
        except ValidationError as exc:
            raise ValueError(f"Updated configuration rejected: {exc}") from exc
    source_evidence = list(source_evidence or [])
    evidence_ids = list(dict.fromkeys([*(evidence_ids or []), *(
        item.evidence_id for item in source_evidence
    )]))
    changes: List[Dict[str, Any]] = []
    applicability_changes: List[Dict[str, Any]] = []

    if status == "failed":
        if updated_configuration is not None:
            raise ValueError("A failed source refresh cannot include an updated configuration")
        if not failure_reason or not failure_reason.strip():
            raise ValueError("A failed source refresh must record failure_reason")
    elif status == "geometry_changed":
        if updated_configuration is None:
            raise ValueError(
                "A geometry-changing refresh must include the complete new sourced configuration"
            )
        if updated_configuration.configuration_id != configuration_id:
            raise ValueError("The refreshed revision must keep the configuration_id")
        same_identity = (
            updated_configuration.instrument == base_configuration.instrument
            and updated_configuration.spacecraft.model_dump(mode="json")
            == base_configuration.spacecraft.model_dump(mode="json")
            and updated_configuration.operating_mode == base_configuration.operating_mode
            and updated_configuration.channel.model_dump(mode="json")
            == base_configuration.channel.model_dump(mode="json")
        )
        if not same_identity:
            raise ValueError(
                "A new source revision cannot change the configuration's instrument, "
                "spacecraft, operating mode, or channel"
            )
        if updated_configuration.revision == base_revision:
            raise ValueError("A changed configuration must use a new immutable revision")
        if updated_configuration.source_revision.revision_id != checked_source_revision_id:
            raise ValueError(
                "The updated configuration source_revision must match the checked source revision"
            )
        changes = _geometry_changes(base_configuration, updated_configuration)
        if not changes:
            raise ValueError(
                "The source check says geometry_changed but no geometry parameter changed"
            )
        applicability_changes = _applicability_changes(
            base_configuration, updated_configuration
        )
        _validate_applicability_refresh(
            applicability_changes, source_evidence, updated_configuration
        )
        supported_by_refresh = {
            parameter
            for item in source_evidence
            for parameter in item.supports
        }
        unsupported_changes = sorted(
            change["parameter"]
            for change in changes
            if change["parameter"] not in supported_by_refresh
        )
        if unsupported_changes:
            raise ValueError(
                "Refresh evidence must support every changed parameter: "
                + ", ".join(unsupported_changes)
            )
        _validate_revised_assessment(
            base_configuration, updated_configuration, changes, source_evidence
        )
    elif status == "applicability_changed":
        if updated_configuration is None:
            raise ValueError(
                "An applicability-changing refresh must include the complete new "
                "sourced configuration"
            )
        if updated_configuration.configuration_id != configuration_id:
            raise ValueError("The refreshed revision must keep the configuration_id")
        same_identity = (
            updated_configuration.instrument == base_configuration.instrument
            and updated_configuration.spacecraft.model_dump(mode="json")
            == base_configuration.spacecraft.model_dump(mode="json")
            and updated_configuration.operating_mode == base_configuration.operating_mode
            and updated_configuration.channel.model_dump(mode="json")
            == base_configuration.channel.model_dump(mode="json")
        )
        if not same_identity:
            raise ValueError(
                "A new source revision cannot change the configuration's instrument, "
                "spacecraft, operating mode, or channel"
            )
        if updated_configuration.revision == base_revision:
            raise ValueError("A changed configuration must use a new immutable revision")
        if updated_configuration.source_revision.revision_id != checked_source_revision_id:
            raise ValueError(
                "The updated configuration source_revision must match the checked source revision"
            )
        changes = _geometry_changes(base_configuration, updated_configuration)
        if changes:
            raise ValueError(
                "The source check says applicability_changed but geometry also "
                "changed; use geometry_changed"
            )
        applicability_changes = _applicability_changes(
            base_configuration, updated_configuration
        )
        if not applicability_changes:
            raise ValueError(
                "The source check says applicability_changed but no applicability "
                "parameter changed"
            )
        _validate_applicability_refresh(
            applicability_changes, source_evidence, updated_configuration
        )
        _validate_revised_assessment(
            base_configuration, updated_configuration, changes, source_evidence
        )
    elif updated_configuration is not None:
        raise ValueError(
            "Only a geometry_changed or applicability_changed source check can "
            "include an updated configuration"
        )

    if updated_configuration is not None:
        retained_evidence = {
            item.evidence_id: item.model_dump(mode="json")
            for item in updated_configuration.evidence
        }
        mismatched_evidence = [
            item.evidence_id
            for item in source_evidence
            if retained_evidence.get(item.evidence_id)
            != item.model_dump(mode="json")
        ]
        if mismatched_evidence:
            raise ValueError(
                "Updated configuration must retain the exact source evidence "
                "supplied with the refresh: "
                + ", ".join(sorted(mismatched_evidence))
            )

    if status in (
        "unchanged",
        "documentation_changed",
        "geometry_changed",
        "applicability_changed",
    ):
        if not evidence_ids:
            raise ValueError(f"A {status} source check must include source evidence")
        if updated_configuration is not None:
            known = {item.evidence_id for item in updated_configuration.evidence}
        else:
            known = {item.evidence_id for item in base_configuration.evidence}
            known.update(item.evidence_id for item in source_evidence)
        missing = sorted(set(evidence_ids) - known)
        if missing:
            raise ValueError(
                "Source refresh references evidence not retained with its revision: "
                + ", ".join(missing)
            )
    if updated_configuration is not None:
        if not {item.evidence_id for item in source_evidence}.issubset(
            set(updated_configuration.source_revision.evidence_ids)
        ):
            raise ValueError(
                "The refreshed source_revision must retain all source-change evidence"
            )

    check_payload = {
        "kind": "explicit_refresh",
        "status": status,
        "checked_at_utc": checked_at_utc,
        "checked_source_revision_id": checked_source_revision_id,
        "reported_by": reported_by,
        "summary": summary,
        "evidence_ids": evidence_ids,
        "source_evidence": source_evidence,
        "failure_reason": failure_reason,
        "new_revision": (
            updated_configuration.revision
            if updated_configuration is not None
            else None
        ),
        "geometry_changes": changes,
        "applicability_changes": applicability_changes,
    }
    digest_input = _canonical_payload_bytes(
        {
            "configuration_id": key[0],
            "base_revision": key[1],
            **check_payload,
            "checked_at_utc": checked_at_utc.isoformat(),
            "source_evidence": [
                item.model_dump(mode="json") for item in source_evidence
            ],
            "updated_configuration": (
                updated_configuration.portable()
                if updated_configuration is not None
                else None
            ),
        },
    )
    check_payload["check_id"] = "refresh-" + hashlib.sha256(
        digest_input
    ).hexdigest()[:16]
    try:
        check = SourceChangeCheck.model_validate(check_payload)
    except ValidationError as exc:
        raise ValueError(f"Invalid source refresh record: {exc}") from exc
    record = check.model_dump(mode="json")

    state = _current_refresh_state()
    with state.lock:
        records = state.checks.get(key, [])
        existing_record = next(
            (item for item in records if item["check_id"] == record["check_id"]),
            None,
        )
        duplicate_record = existing_record is not None
        if duplicate_record and existing_record != record:
            raise ValueError("Refresh check ID collision with different check content")
        candidate_key = None
        existing_candidate = False
        candidate_entry = None
        if updated_configuration is not None:
            candidate_key = (
                updated_configuration.configuration_id,
                updated_configuration.revision,
            )
            existing_candidate = any(
                (configuration.configuration_id, configuration.revision) == candidate_key
                for configuration, _ in state.candidates
            )
            static_keys = {
                (configuration.configuration_id, configuration.revision)
                for configuration, _ in _load_entries()
            }
            if candidate_key in static_keys or (existing_candidate and not duplicate_record):
                raise ValueError(
                    "Configuration IDs and revisions are immutable and must be unique"
                )
        if updated_configuration is not None and not existing_candidate:
            candidate_entry = (
                updated_configuration,
                {
                    "registry_status": "source_refresh_candidate",
                    "source_change_checks": [record],
                    "source_change_notices": [record],
                    "refresh_uncertainty": [],
                    "base_revision": base_revision,
                },
            )
        if not duplicate_record:
            total_records = sum(len(items) for items in state.checks.values())
            if total_records >= MAX_REFRESH_RECORDS:
                raise ValueError(
                    f"Refresh record capacity ({MAX_REFRESH_RECORDS}) has been reached"
                )
        if candidate_entry is not None and len(state.candidates) >= MAX_REFRESH_CANDIDATES:
            raise ValueError(
                f"Refresh candidate capacity ({MAX_REFRESH_CANDIDATES}) has been reached"
            )
        if not duplicate_record or candidate_entry is not None:
            payload_bytes = _serialized_refresh_state_bytes(
                state,
                extra_record=(key, record) if not duplicate_record else None,
                extra_candidate=candidate_entry,
            )
            if payload_bytes > MAX_REFRESH_STATE_BYTES:
                raise ValueError(
                    "Serialized refresh state capacity exceeded: "
                    f"{payload_bytes} bytes would exceed {MAX_REFRESH_STATE_BYTES} bytes"
                )
        if not duplicate_record:
            state.checks.setdefault(key, []).append(record)
        if candidate_entry is not None:
            state.candidates.append(candidate_entry)

    return {
        "status": status,
        "base_configuration_id": configuration_id,
        "base_revision": base_revision,
        "source_change_check": record,
        "idempotent_replay": duplicate_record,
        "geometry_changes": changes,
        "applicability_changes": applicability_changes,
        "new_revision_candidate": (
            {
                "configuration_id": updated_configuration.configuration_id,
                "revision": updated_configuration.revision,
                "registry_status": "source_refresh_candidate",
                "shared_registry_update_required": True,
            }
            if updated_configuration is not None
            else None
        ),
        "source_refresh_persistence": (
            "This check and any candidate revision remain in memory for this server "
            "process. A shared registry revision becomes durable through a reviewed "
            "repository change."
        ),
    }


def _matches(value: Optional[str], candidates: List[str]) -> bool:
    if value is None:
        return True
    query = value.strip().casefold()
    return bool(query) and any(query in candidate.casefold() for candidate in candidates)


def _matches_exact(value: Optional[str], candidate: str) -> bool:
    return value is None or (bool(value.strip()) and value.strip() == candidate)


def lookup_instrument_configurations(
    instrument: Optional[str] = None,
    spacecraft: Optional[str] = None,
    operating_mode: Optional[str] = None,
    configuration_id: Optional[str] = None,
    revision: Optional[str] = None,
) -> Dict[str, Any]:
    """Return matching revisions without selecting one for a study."""
    filters = (instrument, spacecraft, operating_mode, configuration_id, revision)
    if not any(value is not None and value.strip() for value in filters):
        raise ValueError(
            "Provide at least one of instrument, spacecraft, operating_mode, "
            "configuration_id, or revision to search the registry"
        )

    candidates: List[Dict[str, Any]] = []
    for configuration, registry_metadata in _entries_with_refreshes():
        if not _matches(instrument, [configuration.instrument]):
            continue
        if not _matches(
            spacecraft,
            [configuration.spacecraft.name, *configuration.spacecraft.aliases],
        ):
            continue
        if not _matches(operating_mode, [configuration.operating_mode]):
            continue
        if not _matches(configuration_id, [configuration.configuration_id]):
            continue
        if not _matches_exact(revision, configuration.revision):
            continue

        candidates.append(
            {
                "configuration_id": configuration.configuration_id,
                "revision": configuration.revision,
                "instrument": configuration.instrument,
                "spacecraft": configuration.spacecraft.model_dump(mode="json"),
                "operating_mode": configuration.operating_mode,
                "applicability": configuration.applicability.model_dump(mode="json"),
                "geometry": configuration.geometry.model_dump(mode="json"),
                "evidence": [item.model_dump(mode="json") for item in configuration.evidence],
                "geometry_checks": [
                    item.model_dump(mode="json") for item in configuration.geometry_checks
                ],
                "limitations": list(configuration.interpretation.limitations),
                "configuration": configuration.portable(),
                **registry_metadata,
            }
        )

    candidates.sort(key=lambda item: (item["configuration_id"], item["revision"]))
    count = len(candidates)
    return {
        "status": "candidates_found" if count else "no_match",
        "candidate_count": count,
        "ambiguous": count > 1,
        "selection_required": True,
        "selection": None,
        "message": (
            "Choose an exact configuration_id and revision before preparing a study. "
            "The lookup did not select a revision."
            if count
            else "No reviewed shared configuration matches those filters."
        ),
        "candidates": candidates,
    }


def resolve_instrument_configuration(
    configuration_id: str, revision: Optional[str]
) -> Tuple[ConicalConfiguration, Dict[str, Any]]:
    """Resolve an exact revision; never infer a latest revision."""
    if not configuration_id or not configuration_id.strip():
        raise ValueError("configuration_id must be a non-empty registry ID")
    if not revision or not revision.strip():
        matches = [
            configuration.revision
            for configuration, _ in _entries_with_refreshes()
            if configuration.configuration_id == configuration_id.strip()
        ]
        raise ValueError(
            "configuration_revision is required for registry selection. "
            "Choose one exact revision returned by the configuration lookup tool. "
            f"Available revisions: {', '.join(matches) or 'none'}"
        )
    for configuration, metadata in _entries_with_refreshes():
        if (
            configuration.configuration_id == configuration_id.strip()
            and configuration.revision == revision.strip()
        ):
            selection = {
                "source": metadata["registry_status"],
                "configuration_id": configuration.configuration_id,
                "revision": configuration.revision,
                "selection": "explicit_exact_revision",
                "source_change_checks": metadata["source_change_checks"],
                "source_change_notices": metadata["source_change_notices"],
                "refresh_uncertainty": metadata["refresh_uncertainty"],
                "current_source_status": metadata["current_source_status"],
                "superseded_refresh_failures": metadata[
                    "superseded_refresh_failures"
                ],
                "base_revision": metadata.get("base_revision"),
                "shared_registry_update_required": (
                    metadata["registry_status"] == "source_refresh_candidate"
                ),
            }
            return configuration, selection
    raise ValueError(
        f"No configuration revision {revision!r} exists for registry ID "
        f"{configuration_id!r}. Use the configuration lookup tool to list candidates."
    )
