"""The CI gate: select scoped cases from a snapshot diff, run them, classify.

The gate selects cases with scoped_cases.select_cases, runs eval.run_eval
through a subprocess with the recorded HTTP cache, and classifies the
result rows: a scored failure blocks, an infrastructure failure retries
once and then records the run as unmeasured without blocking, and an
empty selection skips the eval entirely.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval import ci, run_eval


def _tool(bare_name, description="original description"):
    return {
        "type": "function",
        "function": {
            "name": bare_name + run_eval.SUFFIX,
            "description": description,
            "parameters": {"type": "object"},
        },
    }


def _snapshot():
    return {
        "tools": [_tool("get_access_cells"), _tool("export_study")],
        "server_instructions": "server instructions",
    }


def _with_description(snapshot, bare_name, description="changed description"):
    changed = json.loads(json.dumps(snapshot))
    for tool in changed["tools"]:
        if tool["function"]["name"] == bare_name + run_eval.SUFFIX:
            tool["function"]["description"] = description
    return changed


def _write_snapshots(tmp_path, before, after):
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_path.write_text(json.dumps(before))
    after_path.write_text(json.dumps(after))
    return before_path, after_path


def _ok_row(case):
    return {"case": case, "status": "ok", "passed": True}


def _failed_row(case):
    return {"case": case, "status": "ok", "passed": False}


def _infra_row(case):
    return {"case": case, "status": "infra_error", "error_category": "timeout"}


def _fake_runner(tmp_path, rows_by_attempt, calls):
    def run_cases(cases, *, attempt, timeout_seconds):
        calls.append(
            {"cases": list(cases), "attempt": attempt, "timeout": timeout_seconds}
        )
        rows = rows_by_attempt.get(attempt)
        if rows is None:
            return 1, None
        path = tmp_path / f"attempt-{attempt}.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return 0, path

    return run_cases


# --- classification -------------------------------------------------------


def test_all_passed_rows_classify_as_passed():
    rows = [_ok_row("fetch_gaps")]
    assert ci.classify(rows, ["fetch_gaps"]) == ci.PASSED


def test_a_failed_check_is_a_checker_failure_even_beside_infra():
    rows = [_ok_row("fetch_gaps"), _failed_row("point_study_explicit"), _infra_row("short_given")]
    assert ci.classify(rows, ["fetch_gaps", "point_study_explicit", "short_given"]) == (
        ci.CHECKER_FAILURE
    )


def test_infra_error_rows_classify_as_infra_failure():
    rows = [_ok_row("fetch_gaps"), _infra_row("point_study_explicit")]
    assert ci.classify(rows, ["fetch_gaps", "point_study_explicit"]) == ci.INFRA_FAILURE


def test_a_case_without_rows_classify_as_infra_failure():
    rows = [_ok_row("fetch_gaps")]
    assert ci.classify(rows, ["fetch_gaps", "point_study_explicit"]) == ci.INFRA_FAILURE


def test_no_rows_classify_as_a_harness_failure():
    assert ci.classify([], ["fetch_gaps"]) == ci.HARNESS_FAILURE


# --- selection entry point ------------------------------------------------


def test_select_prints_the_cases_and_gaps_as_json(tmp_path, capsys):
    before, after = _write_snapshots(
        tmp_path,
        _snapshot(),
        _with_description(_snapshot(), "get_access_cells"),
    )
    exit_code = ci.main(["select", "--before", str(before), "--after", str(after)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload == {"cases": ["fetch_gaps"], "gaps": []}


def test_select_reports_gaps_without_cases(tmp_path, capsys):
    before, after = _write_snapshots(
        tmp_path, _snapshot(), _with_description(_snapshot(), "export_study")
    )
    exit_code = ci.main(["select", "--before", str(before), "--after", str(after)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["cases"] == []
    assert [gap["tool"] for gap in payload["gaps"]] == ["export_study"]
    assert payload["gaps"][0]["reason"]


def test_select_fails_on_an_unmapped_tool(tmp_path, capsys):
    after = _snapshot()
    after["tools"].append(_tool("unmapped_tool"))
    before, after = _write_snapshots(tmp_path, _snapshot(), after)
    exit_code = ci.main(["select", "--before", str(before), "--after", str(after)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "unmapped_tool" in captured.err


def test_select_on_identical_snapshots_selects_nothing(tmp_path, capsys):
    snapshot = _snapshot()
    before, after = _write_snapshots(tmp_path, snapshot, snapshot)
    exit_code = ci.main(["select", "--before", str(before), "--after", str(after)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload == {"cases": [], "gaps": []}


# --- the gate -------------------------------------------------------------


def test_gate_runs_the_selected_cases_and_passes(tmp_path, monkeypatch):
    before, after = _write_snapshots(
        tmp_path,
        _snapshot(),
        _with_description(_snapshot(), "get_access_cells"),
    )
    calls = []
    monkeypatch.setattr(
        ci,
        "_run_eval",
        _fake_runner(tmp_path, {1: [_ok_row("fetch_gaps")]}, calls),
    )
    logs = []
    payload = ci.run_gate(before, after, run_cases=ci._run_eval, log=logs.append)

    assert payload["verdict"] == ci.PASSED
    assert [call["cases"] for call in calls] == [["fetch_gaps"]]
    assert calls[0]["timeout"] > 0
    assert any(line.startswith("gate: ") for line in logs)
    gate_line = [line for line in logs if line.startswith("gate: ")][-1]
    assert json.loads(gate_line.removeprefix("gate: "))["cases"] == ["fetch_gaps"]


def test_gate_main_exit_codes(tmp_path, monkeypatch, capsys):
    before, after = _write_snapshots(
        tmp_path,
        _snapshot(),
        _with_description(_snapshot(), "get_access_cells"),
    )
    monkeypatch.setattr(
        ci, "_run_eval", _fake_runner(tmp_path, {1: [_ok_row("fetch_gaps")]}, [])
    )
    assert ci.main(["gate", "--before", str(before), "--after", str(after)]) == 0

    monkeypatch.setattr(
        ci, "_run_eval", _fake_runner(tmp_path, {1: [_failed_row("fetch_gaps")]}, [])
    )
    assert ci.main(["gate", "--before", str(before), "--after", str(after)]) == 1


def test_gate_never_runs_the_suite_without_selected_cases(tmp_path, monkeypatch):
    before, after = _write_snapshots(
        tmp_path, _snapshot(), _with_description(_snapshot(), "export_study")
    )

    def unexpected(*_args, **_kwargs):
        raise AssertionError("the gate ran the eval without selected cases")

    monkeypatch.setattr(ci, "_run_eval", unexpected)
    logs = []
    payload = ci.run_gate(before, after, run_cases=unexpected, log=logs.append)

    assert payload["verdict"] == ci.SKIPPED
    assert payload["attempts"] == []
    assert any("export_study" in line for line in logs)


def test_gate_blocks_on_a_checker_failure_without_retrying(tmp_path, monkeypatch):
    before, after = _write_snapshots(
        tmp_path,
        _snapshot(),
        _with_description(_snapshot(), "get_access_cells"),
    )
    calls = []
    monkeypatch.setattr(
        ci, "_run_eval", _fake_runner(tmp_path, {1: [_failed_row("fetch_gaps")]}, calls)
    )
    payload = ci.run_gate(before, after, run_cases=ci._run_eval)

    assert payload["verdict"] == ci.CHECKER_FAILURE
    assert len(calls) == 1


def test_gate_retries_an_infrastructure_failure_once_and_passes(tmp_path, monkeypatch):
    before, after = _write_snapshots(
        tmp_path,
        _snapshot(),
        _with_description(_snapshot(), "get_access_cells"),
    )
    calls = []
    monkeypatch.setattr(
        ci,
        "_run_eval",
        _fake_runner(
            tmp_path,
            {1: [_infra_row("fetch_gaps")], 2: [_ok_row("fetch_gaps")]},
            calls,
        ),
    )
    payload = ci.run_gate(before, after, run_cases=ci._run_eval)

    assert payload["verdict"] == ci.PASSED
    assert [call["attempt"] for call in calls] == [1, 2]


def test_gate_records_a_second_infrastructure_failure_as_unmeasured(
    tmp_path, monkeypatch, capsys
):
    before, after = _write_snapshots(
        tmp_path,
        _snapshot(),
        _with_description(_snapshot(), "get_access_cells"),
    )
    calls = []
    monkeypatch.setattr(
        ci,
        "_run_eval",
        _fake_runner(tmp_path, {1: [_infra_row("fetch_gaps")], 2: [_infra_row("fetch_gaps")]}, calls),
    )
    exit_code = ci.main(["gate", "--before", str(before), "--after", str(after)])

    payload = json.loads(capsys.readouterr().out.splitlines()[-1].removeprefix("gate: "))
    assert exit_code == 0
    assert payload["verdict"] == ci.UNMEASURED
    assert [call["attempt"] for call in calls] == [1, 2]


def test_gate_blocks_when_the_harness_produces_no_rows(tmp_path, monkeypatch):
    before, after = _write_snapshots(
        tmp_path,
        _snapshot(),
        _with_description(_snapshot(), "get_access_cells"),
    )
    calls = []
    monkeypatch.setattr(ci, "_run_eval", _fake_runner(tmp_path, {1: None}, calls))
    payload = ci.run_gate(before, after, run_cases=ci._run_eval)

    assert payload["verdict"] == ci.HARNESS_FAILURE
    assert len(calls) == 1


def test_gate_writes_a_summary_file(tmp_path, monkeypatch):
    before, after = _write_snapshots(
        tmp_path, _snapshot(), _with_description(_snapshot(), "export_study")
    )
    summary = tmp_path / "summary.md"
    ci.run_gate(before, after, run_cases=lambda *_a, **_k: (0, None), summary=summary)

    text = summary.read_text()
    assert "skipped" in text
    assert "export_study" in text


def test_gate_reports_a_selection_error_without_running(tmp_path, monkeypatch, capsys):
    after = _snapshot()
    after["tools"].append(_tool("unmapped_tool"))
    before, after = _write_snapshots(tmp_path, _snapshot(), after)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("the gate ran the eval on a selection error")

    monkeypatch.setattr(ci, "_run_eval", unexpected)
    exit_code = ci.main(["gate", "--before", str(before), "--after", str(after)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "unmapped_tool" in captured.out


def test_gate_skips_identical_snapshots_without_running(tmp_path, monkeypatch):
    snapshot = _snapshot()
    before, after = _write_snapshots(tmp_path, snapshot, snapshot)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("the gate ran the eval on an unchanged snapshot")

    monkeypatch.setattr(ci, "_run_eval", unexpected)
    payload = ci.run_gate(before, after, run_cases=unexpected)

    assert payload["verdict"] == ci.SKIPPED


# --- the default runner ---------------------------------------------------


def test_the_runner_builds_the_recorded_eval_command(tmp_path, monkeypatch):
    recorded = {}

    def fake_run(command, cwd, check, timeout):
        recorded.update(
            command=command, cwd=cwd, check=check, timeout=timeout
        )
        result = Path(command[command.index("--result-out") + 1])
        result.write_text(json.dumps(_ok_row("fetch_gaps")) + "\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(ci, "RESULTS", tmp_path)
    monkeypatch.setattr(ci.subprocess, "run", fake_run)

    returncode, result_path = ci._run_eval(
        ["fetch_gaps"], attempt=1, timeout_seconds=120.0
    )

    assert returncode == 0
    command = recorded["command"]
    assert command[:3] == [sys.executable, "-m", "eval.run_eval"]
    cases = command[command.index("--cases") + 1 : command.index("--offline")]
    assert cases == ["fetch_gaps"]
    assert "--offline" in command
    assert float(command[command.index("--conversation-timeout") + 1]) > 0
    assert recorded["timeout"] == 120.0
    assert recorded["cwd"] == ci.SERVER_DIR
    assert result_path == ci.RESULTS / "ci-scoped-attempt-1.jsonl"
