"""Map server tools to scoped eval cases and select cases from a diff.

The mapping keys are bare server tool names. The snapshot records tools
under LibreChat's ``_mcp_tatc``-suffixed names, and the selector strips
that suffix before lookup. Case IDs are validated against
``cases.build_cases`` rather than a second hard-coded list.
"""

import copy
import json
from datetime import datetime, timezone

import pytest

from eval import run_eval
from eval.cases import Clock, build_cases
from eval.scoped_cases import (
    DEFAULT_CASE_ID,
    MAX_CASES,
    TOOL_CASES,
    NoCase,
    select_cases,
)


def _snapshot():
    return json.loads(run_eval.SNAPSHOT.read_text())


def _tool(snapshot, bare_name):
    return next(
        tool
        for tool in snapshot["tools"]
        if tool["function"]["name"] == bare_name + run_eval.SUFFIX
    )


def _with_description(snapshot, bare_name, description):
    changed = copy.deepcopy(snapshot)
    _tool(changed, bare_name)["function"]["description"] = description
    return changed


def _case_ids():
    clock = Clock(datetime(2026, 9, 18, 4, 0, tzinfo=timezone.utc))
    return {case.id for case in build_cases(clock)}


def test_every_server_tool_has_a_mapping_entry():
    names = {tool["function"]["name"] for tool in _snapshot()["tools"]}
    assert not any(name.endswith(run_eval.SUFFIX) for name in TOOL_CASES), (
        "mapping keys are bare server tool names without the _mcp_tatc suffix"
    )
    missing = {name.removesuffix(run_eval.SUFFIX) for name in names} - set(TOOL_CASES)
    assert not missing, f"tools without a scoped-case entry: {sorted(missing)}"
    extra = set(TOOL_CASES) - {name.removesuffix(run_eval.SUFFIX) for name in names}
    assert not extra, f"mapping entries that are not server tools: {sorted(extra)}"


def test_mapped_case_ids_exist_in_build_cases():
    case_ids = _case_ids()
    for name, entry in TOOL_CASES.items():
        if isinstance(entry, NoCase):
            assert entry.reason.strip(), f"{name} states no reason for its gap"
        else:
            assert entry in case_ids, f"{name} maps to unknown case {entry!r}"


def test_default_case_is_the_legacy_coverage_case():
    assert DEFAULT_CASE_ID == "short_given"
    assert DEFAULT_CASE_ID in _case_ids()


def test_identical_snapshots_select_no_cases():
    before = _snapshot()
    selection = select_cases(before, copy.deepcopy(before))
    assert selection.cases == []
    assert selection.gaps == []


def test_a_description_change_selects_that_tools_case():
    before = _snapshot()
    after = _with_description(before, "analyze_coverage", "Changed description.")
    selection = select_cases(before, after)
    assert selection.cases == ["point_study_explicit"]
    assert selection.gaps == []


def test_a_schema_change_selects_that_tools_case():
    before = _snapshot()
    after = copy.deepcopy(before)
    _tool(after, "get_access_cells")["function"]["parameters"] = {"type": "object"}
    selection = select_cases(before, after)
    assert selection.cases == ["fetch_gaps"]
    assert selection.gaps == []


def test_a_removed_tool_selects_its_case():
    before = _snapshot()
    after = copy.deepcopy(before)
    after["tools"] = [
        tool
        for tool in after["tools"]
        if tool["function"]["name"] != "get_access_cells" + run_eval.SUFFIX
    ]
    selection = select_cases(before, after)
    assert selection.cases == ["fetch_gaps"]
    assert selection.gaps == []


def test_a_removed_tool_without_a_table_entry_uses_the_default_case(monkeypatch):
    before = _snapshot()
    after = copy.deepcopy(before)
    after["tools"] = [
        tool
        for tool in after["tools"]
        if tool["function"]["name"] != "get_access_cells" + run_eval.SUFFIX
    ]
    monkeypatch.delitem(TOOL_CASES, "get_access_cells")
    selection = select_cases(before, after)
    assert selection.cases == [DEFAULT_CASE_ID]
    assert selection.gaps == []


def test_a_renamed_tool_keeps_the_change_selectable(monkeypatch):
    before = _snapshot()
    after = copy.deepcopy(before)
    _tool(after, "get_access_cells")["function"]["name"] = (
        "renamed_cells" + run_eval.SUFFIX
    )
    monkeypatch.delitem(TOOL_CASES, "get_access_cells")
    monkeypatch.setitem(TOOL_CASES, "renamed_cells", "fetch_gaps")
    selection = select_cases(before, after)
    assert "fetch_gaps" in selection.cases
    assert DEFAULT_CASE_ID in selection.cases
    assert selection.gaps == []


def test_an_added_tool_selects_its_case(monkeypatch):
    before = _snapshot()
    after = copy.deepcopy(before)
    added = copy.deepcopy(_tool(after, "get_access_cells"))
    added["function"]["name"] = "new_tool" + run_eval.SUFFIX
    after["tools"].append(added)
    # A new tool cannot reach a scoped eval unmapped: the completeness test
    # fails until TOOL_CASES has an entry, so simulate that entry here.
    monkeypatch.setitem(TOOL_CASES, "new_tool", "short_given")
    selection = select_cases(before, after)
    assert selection.cases == ["short_given"]
    assert selection.gaps == []


def test_duplicate_cases_are_removed():
    before = _snapshot()
    after = _with_description(before, "prepare_conical_study", "Changed description.")
    after = _with_description(after, "finish_conical_study", "Changed description.")
    selection = select_cases(before, after)
    assert selection.cases == ["conical_amsr2_complete"]
    assert selection.gaps == []


def test_the_selection_is_capped_at_three_cases():
    before = _snapshot()
    after = before
    for name in (
        "analyze_coverage",
        "get_access_cells",
        "prepare_conical_study",
        "get_mixed_events",
        "get_pointed_events",
    ):
        after = _with_description(after, name, "Changed description.")
    selection = select_cases(before, after)
    assert len(selection.cases) == MAX_CASES == 3
    assert len(set(selection.cases)) == len(selection.cases)
    assert set(selection.cases) <= {
        "point_study_explicit",
        "fetch_gaps",
        "conical_amsr2_complete",
        "mixed_amsr2_s1c_complete",
        "pointed_s1c_iw_complete",
    }


def test_a_server_instructions_change_selects_the_default_case():
    before = _snapshot()
    after = copy.deepcopy(before)
    after["server_instructions"] += "\nOne added instruction sentence."
    selection = select_cases(before, after)
    assert selection.cases == ["short_given"]
    assert selection.gaps == []


def test_tool_and_instruction_changes_combine():
    before = _snapshot()
    after = _with_description(before, "analyze_coverage", "Changed description.")
    after["server_instructions"] += "\nOne added instruction sentence."
    selection = select_cases(before, after)
    assert selection.cases == ["point_study_explicit", "short_given"]
    assert selection.gaps == []


def test_a_no_case_tool_change_is_reported_as_a_gap():
    before = _snapshot()
    after = _with_description(before, "export_study", "Changed description.")
    selection = select_cases(before, after)
    assert selection.cases == []
    assert [gap.tool for gap in selection.gaps] == ["export_study"]
    assert selection.gaps[0].reason == TOOL_CASES["export_study"].reason


def test_a_gap_and_a_case_change_combine():
    before = _snapshot()
    after = _with_description(before, "export_study", "Changed description.")
    after = _with_description(after, "analyze_coverage", "Changed description.")
    selection = select_cases(before, after)
    assert selection.cases == ["point_study_explicit"]
    assert [gap.tool for gap in selection.gaps] == ["export_study"]


def test_an_unmapped_tool_is_an_error():
    before = _snapshot()
    after = copy.deepcopy(before)
    added = copy.deepcopy(_tool(after, "get_access_cells"))
    added["function"]["name"] = "unmapped_tool" + run_eval.SUFFIX
    after["tools"].append(added)
    with pytest.raises(ValueError, match="unmapped_tool"):
        select_cases(before, after)
