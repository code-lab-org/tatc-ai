"""The eval's streaming judge, offline: LibreChat drops tool calls from streams without a role."""

from datetime import datetime

from eval.cases import Clock, build_cases
from eval.run_eval import judge_stream


def _chunk(**delta):
    return {"choices": [{"delta": delta}]}


TOOL_CALL = {"tool_calls": [{"index": 0, "function": {"name": "search_satellites"}}]}


def test_stream_with_role_and_tool_call_is_ok():
    assert judge_stream([_chunk(role="assistant", content=""), _chunk(**TOOL_CALL)]) == "ok"


def test_role_on_first_delta_is_required():
    # The ASU proxy's DeepSeek stream starts with tool_calls and no role.
    assert judge_stream([_chunk(**TOOL_CALL), _chunk(role="assistant")]) == "missing role"


def test_stream_without_tool_call_is_reported():
    assert judge_stream([_chunk(role="assistant", content="Hi")]) == "no tool call"


def test_chunks_without_choices_are_skipped():
    assert judge_stream([{"choices": []}, _chunk(role="assistant"), _chunk(**TOOL_CALL)]) == "ok"
    assert judge_stream([{"choices": []}]) == "missing role"


def test_geometry_eval_inputs_are_synthetic_study_assumptions():
    cases = build_cases(Clock(datetime(2026, 1, 1)))
    geometry_prompts = [
        case.prompt
        for case in cases
        if "geometry_given" in case.tags or case.id == "fetch_gaps"
    ]
    assert geometry_prompts
    assert all("synthetic study assumption" in prompt.lower() for prompt in geometry_prompts)
    assert all("nasa modis design page" not in prompt.lower() for prompt in geometry_prompts)
    assert all("usgs landsat 9 page" not in prompt.lower() for prompt in geometry_prompts)
