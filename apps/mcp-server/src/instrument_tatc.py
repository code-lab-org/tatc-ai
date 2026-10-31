"""Dispatch generic single-instrument studies to their TAT-C mappings."""

from __future__ import annotations

from typing import Any, Dict, Sequence

from .conical_config import ConicalConfiguration
from .conical_tatc import collect_cell_events as collect_conical_cell_events
from .conical_tatc import resolved_snapshot as resolved_conical_snapshot
from .pointed_config import PointedConfiguration
from .pointed_tatc import collect_pointed_cell_events, resolved_pointed_snapshot


def resolved_snapshot(configuration: Any, omm_records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Resolve the configuration through its public TAT-C instrument adapter."""
    if isinstance(configuration, PointedConfiguration):
        return resolved_pointed_snapshot(configuration, omm_records)
    if isinstance(configuration, ConicalConfiguration):
        return resolved_conical_snapshot(configuration, omm_records)
    raise ValueError(f"Unsupported instrument configuration {type(configuration).__name__}")


def collect_cell_events(task: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Keep one cell-task interface for both existing and pointed studies."""
    configuration = task.get("configuration", {})
    version = configuration.get("schema_version")
    try:
        if version == "pointed-configuration-v1":
            return collect_pointed_cell_events(task)
        if version == "conical-configuration-v1":
            return collect_conical_cell_events(task)
        raise ValueError(f"Unsupported instrument configuration schema_version {version!r}")
    except Exception as exc:
        raise ValueError(
            f"Configuration {configuration.get('configuration_id')!r}, "
            f"revision {configuration.get('revision')!r}, "
            f"cell {task.get('cell_id')}: {exc}"
        ) from exc
