"""The eval's streaming judge, offline: LibreChat drops tool calls from streams without a role."""

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
