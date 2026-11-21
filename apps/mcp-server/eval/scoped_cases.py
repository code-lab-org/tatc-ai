"""Map the model-facing tool surface to scoped eval cases.

A model-facing change to one tool should run the eval cases that
exercise that tool, not the full suite. TOOL_CASES maps every server
tool name to the case whose checks call it or read its result, to a
tuple of such cases, or to NoCase with the reason no case covers it.
select_cases diffs two model-facing snapshots (the payload
eval.run_eval.model_surface builds and eval/fixtures/model_surface.json
records) and returns the affected cases plus the touched gaps, so a
change with no covering case is reported instead of silently skipped.

Naming convention: mapping keys are bare server tool names. Snapshot
tool names carry LibreChat's ``_mcp_tatc`` suffix; the selector strips
it before lookup. An instructions-only change selects DEFAULT_CASE_ID,
the legacy coverage case, and at most MAX_CASES cases run per change.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Union

from .run_eval import SUFFIX

DEFAULT_CASE_ID = "short_given"
MAX_CASES = 3


@dataclass(frozen=True)
class NoCase:
    """An explicit gap: no eval case exercises this tool yet."""

    reason: str


@dataclass(frozen=True)
class Gap:
    """A touched tool with no covering case."""

    tool: str
    reason: str


@dataclass(frozen=True)
class Selection:
    """The cases to run and the gaps to report."""

    cases: List[str]
    gaps: List[Gap]


TOOL_CASES: Dict[str, Union[str, Tuple[str, ...], NoCase]] = {
    # Satellite and instrument lookup
    "search_satellites": NoCase(
        "no case requires satellite search; point studies resolve names "
        "through get_satellite_info"
    ),
    "get_satellite_info": "point_study_explicit",
    "search_instruments": NoCase(
        "no case requires instrument search; the unclear-geometry case "
        "accepts three other lookup paths"
    ),
    "get_satellite_instruments": NoCase(
        "the unclear-geometry case accepts the resolver or either lookup tool, "
        "so no case requires this one"
    ),
    "get_instrument_info": NoCase(
        "the unclear-geometry case accepts the resolver or either lookup tool, "
        "so no case requires this one"
    ),
    "resolve_instrument_geometry": "resolver_lookup_supported",
    "convert_swath_to_field_of_regard": NoCase(
        "prompts supply geometry values directly; no case requires the converter"
    ),
    # Legacy coverage workflow
    "begin_access_run": "fetch_gaps",
    "set_access_region": "fetch_gaps",
    "finish_access_run": "fetch_gaps",
    "get_access_events": NoCase(
        "the complete case accepts events, daily, or cells; the partial case "
        "accepts events or daily, so no check requires events"
    ),
    "get_access_daily": NoCase(
        "the complete case accepts events, daily, or cells; the partial case "
        "accepts events or daily, so no check requires daily rows"
    ),
    "get_access_cells": "fetch_gaps",
    "get_access_revisit": NoCase("no case checks the legacy revisit view"),
    "get_access_event_spacing": NoCase(
        "no legacy case checks event spacing; pointed and mixed studies "
        "have their own spacing fetch tools"
    ),
    # Registry and conical study workflow
    "lookup_instrument_configurations": "conical_amsr2_registry_selection",
    "record_instrument_configuration_refresh": "conical_amsr2_failed_refresh",
    # The claims case sends a v2 source-claims configuration, the form the
    # advertised schema once rejected; the complete case drives the whole
    # inline-evidence flow. Both exercise prepare_conical_study.
    "prepare_conical_study": ("conical_amsr2_claims", "conical_amsr2_complete"),
    "finish_conical_study": "conical_amsr2_complete",
    # Pointed study workflow
    "prepare_pointed_study": "pointed_s1c_iw_complete",
    "finish_pointed_study": "pointed_s1c_iw_complete",
    "get_pointed_events": "pointed_s1c_iw_complete",
    "get_pointed_daily": "pointed_s1c_iw_complete",
    "get_pointed_cells": "pointed_s1c_iw_complete",
    "get_pointed_revisit": "pointed_s1c_iw_complete",
    "get_pointed_event_spacing": "pointed_s1c_iw_complete",
    # Mixed study workflow
    "prepare_mixed_study": "mixed_amsr2_s1c_complete",
    "finish_mixed_study": "mixed_amsr2_s1c_complete",
    "get_mixed_events": "mixed_amsr2_s1c_complete",
    "get_mixed_daily": "mixed_amsr2_s1c_complete",
    "get_mixed_cells": "mixed_amsr2_s1c_complete",
    "get_mixed_revisit": "mixed_amsr2_s1c_complete",
    "get_mixed_event_spacing": "mixed_amsr2_s1c_complete",
    # Study results and the point-coverage explorer
    "analyze_coverage": "point_study_explicit",
    "get_study_setup": NoCase("no case retrieves a study setup by draft or result ID"),
    "export_study": NoCase("no case exports a study file"),
    "generate_ground_track": NoCase("every coverage case runs a study, not a plain ground track"),
    "ground_track_map": NoCase("no case drives the Cesium map pane"),
    "visualize": NoCase(
        "the explorer case scores analyze_coverage numbers, not the visualization call"
    ),
}


def _bare_name(snapshot_name: str) -> str:
    """Strip LibreChat's tool suffix; a bare name passes through."""
    if snapshot_name.endswith(SUFFIX):
        return snapshot_name[: -len(SUFFIX)]
    return snapshot_name


def _functions(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Map bare tool name to its function entry (name, description, schema)."""
    return {
        _bare_name(tool["function"]["name"]): tool["function"]
        for tool in snapshot.get("tools", [])
    }


def select_cases(before: Dict[str, Any], after: Dict[str, Any]) -> Selection:
    """Return the eval cases and coverage gaps a snapshot change touches.

    Compares tool entries by bare name and the server instructions. A
    changed, added, or removed tool contributes its mapped case; a
    touched NoCase tool is reported as a gap instead. A removed tool
    whose table entry was deleted with it falls back to the default case,
    so a rename stays selectable. An instructions-only change contributes
    the default case. Cases are de-duplicated and capped at MAX_CASES;
    gaps are all reported. Raises ValueError for an added or changed tool
    that TOOL_CASES does not map.
    """
    before_functions = _functions(before)
    after_functions = _functions(after)
    touched = [
        name
        for name, function in after_functions.items()
        if name not in before_functions or before_functions[name] != function
    ]
    touched.extend(name for name in before_functions if name not in after_functions)
    selected: List[str] = []
    gaps: List[Gap] = []
    for name in touched:
        entry = TOOL_CASES.get(name)
        if entry is None:
            if name not in after_functions:
                # The tool was removed, and the table was updated with it.
                # Keep the change selectable with the default case.
                entry = DEFAULT_CASE_ID
            else:
                raise ValueError(
                    f"No scoped-case mapping for tool {name!r}; add a case ID or "
                    "a NoCase reason to eval/scoped_cases.py TOOL_CASES"
                )
        if isinstance(entry, NoCase):
            if not any(gap.tool == name for gap in gaps):
                gaps.append(Gap(name, entry.reason))
        else:
            for case_id in (entry,) if isinstance(entry, str) else entry:
                if case_id not in selected:
                    selected.append(case_id)
    if before.get("server_instructions") != after.get("server_instructions"):
        if DEFAULT_CASE_ID not in selected:
            selected.append(DEFAULT_CASE_ID)
    return Selection(selected[:MAX_CASES], gaps)
