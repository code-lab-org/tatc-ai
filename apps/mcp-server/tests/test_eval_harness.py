"""Offline HTTP-seam checks for the bounded tool-calling eval harness."""

import asyncio
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
import requests

from eval import run_eval
from eval.http_replay import HttpReplay
from src.celestrak_client import get_satellite_info


def test_offline_replay_supports_celestrak_cold_cache(monkeypatch):
    replay = HttpReplay(offline=True)
    monkeypatch.setattr(requests, "get", replay.get)

    result = get_satellite_info("27424")

    assert result["norad_id"] == 27424
    assert result["name"] == "AQUA"
    assert result["omm"][0]["NORAD_CAT_ID"] == 27424
    assert replay.misses == 0


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.attempts += 1
        self.server.behavior(self, body, self.server.attempts)


class _Server:
    def __init__(self, behavior):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.behavior = behavior
        self.httpd.attempts = 0
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True
        )
        self.thread.start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.httpd.server_port}"

    @property
    def attempts(self):
        return self.httpd.attempts

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


def _json_response(handler, payload, status=200):
    raw = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _message(text="ok"):
    return {
        "id": "request-1",
        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        "choices": [{"message": {"role": "assistant", "content": text}}],
    }


def _complete(
    server, *, request_timeout=1.0, conversation_timeout=5.0, record=None
):
    return run_eval.request_completion(
        server.base_url,
        "test-key",
        b"{}",
        request_timeout=request_timeout,
        conversation_deadline=time.monotonic() + conversation_timeout,
        record=record,
    )


def test_default_primary_model_is_validated_against_the_catalog(monkeypatch):
    assert run_eval.DEFAULT_MODELS == ["deepseek-v4-1-flash"]
    args = SimpleNamespace(all_models=False, models=None)
    assert run_eval._selected_models(args) == ["deepseek-v4-1-flash"]
    monkeypatch.setattr(run_eval, "DEFAULT_MODELS", ["not-a-voyager-model"])
    with pytest.raises(ValueError, match="not in the LibreChat ASU Voyager catalog"):
        run_eval._selected_models(args)


def test_explicit_model_selection_is_sent_as_given():
    args = SimpleNamespace(all_models=False, models=["not-a-voyager-model"])
    assert run_eval._selected_models(args) == ["not-a-voyager-model"]


def test_delayed_response_uses_request_budget_without_retry():
    def behavior(handler, _body, _attempt):
        time.sleep(0.8)
        _json_response(handler, _message())

    server = _Server(behavior)
    try:
        records = []
        started = time.monotonic()
        with pytest.raises(run_eval.RequestTimeout):
            _complete(
                server,
                request_timeout=0.2,
                record=lambda **row: records.append(row),
            )
        assert time.monotonic() - started < 0.7
        assert server.attempts == 1
        assert records[-1]["error_category"] == "timeout"
    finally:
        server.close()


def test_trickle_response_cannot_defeat_the_total_deadline():
    def behavior(handler, _body, _attempt):
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", "10000000")
        handler.end_headers()
        try:
            for _ in range(100):
                handler.wfile.write(b"x")
                handler.wfile.flush()
                time.sleep(0.02)
        except (BrokenPipeError, ConnectionResetError):
            pass

    server = _Server(behavior)
    try:
        started = time.monotonic()
        with pytest.raises(run_eval.RequestTimeout):
            _complete(server, request_timeout=0.25)
        assert time.monotonic() - started < 0.8
        assert server.attempts == 1
    finally:
        server.close()


def test_context_marker_past_300_bytes_is_an_overflow_not_an_http_error():
    def behavior(handler, _body, _attempt):
        raw = b"x" * 600 + b" ContextWindowExceeded "
        handler.send_response(400)
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)

    server = _Server(behavior)
    try:
        with pytest.raises(run_eval.ContextOverflow):
            _complete(server)
        assert server.attempts == 1
    finally:
        server.close()


def test_context_marker_in_a_503_is_overflow_and_not_retried():
    def behavior(handler, _body, _attempt):
        raw = b"x" * 600 + b" context length exceeded "
        handler.send_response(503)
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)

    server = _Server(behavior)
    try:
        with pytest.raises(run_eval.ContextOverflow):
            _complete(server)
        assert server.attempts == 1
    finally:
        server.close()


def test_503_retries_once_without_sleeping():
    def behavior(handler, _body, attempt):
        if attempt == 1:
            _json_response(handler, {"error": "busy"}, status=503)
        else:
            _json_response(handler, _message("second attempt"))

    server = _Server(behavior)
    try:
        started = time.monotonic()
        assert _complete(server)["content"] == "second attempt"
        assert time.monotonic() - started < 0.8
        assert server.attempts == 2
    finally:
        server.close()


def test_429_gets_at_most_one_retry():
    def behavior(handler, _body, _attempt):
        _json_response(handler, {"error": "rate limited"}, status=429)

    server = _Server(behavior)
    try:
        with pytest.raises(run_eval.ModelCallError) as caught:
            _complete(server)
        assert caught.value.category == "http_429"
        assert server.attempts == 2
    finally:
        server.close()


def test_client_400_is_not_retried():
    def behavior(handler, _body, _attempt):
        _json_response(handler, {"error": "bad request"}, status=400)

    server = _Server(behavior)
    try:
        with pytest.raises(run_eval.ModelCallError) as caught:
            _complete(server)
        assert caught.value.category == "http_400"
        assert server.attempts == 1
    finally:
        server.close()


def test_connection_failure_retries_once():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    started = time.monotonic()
    with pytest.raises(run_eval.ModelCallError) as caught:
        run_eval.request_completion(
            f"http://127.0.0.1:{port}",
            "test-key",
            b"{}",
            request_timeout=0.3,
            conversation_deadline=time.monotonic() + 2.0,
        )
    assert caught.value.category == "connection"
    assert time.monotonic() - started < 1.0


def test_default_result_record_redacts_prompts_and_tool_text(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from eval.cases import Call, Trace

    class FakeHarness:
        tool_names = ["secret_tool"]

        def __init__(self, *_args, **_kwargs):
            pass

        def converse(self, _model, _prompt, **_kwargs):
            trace = Trace(prompt="SECRET PROMPT")
            trace.turns = 1
            trace.stop = "timeout"
            trace.error_category = "timeout"
            trace.error = "SECRET ERROR"
            trace.calls = [
                Call("secret_tool", {"hidden": "SECRET ARG"}, True, "SECRET TOOL TEXT", None)
            ]
            return trace

    class FakeReplay:
        def __init__(self, *_args, **_kwargs):
            self.recorded_at = datetime(2026, 10, 10, tzinfo=timezone.utc)

        def install(self):
            pass

        def save(self):
            pass

    class FakeCase:
        id = "fake_case"
        tags = ("short",)
        prompt = "SECRET PROMPT"

    monkeypatch.setattr(run_eval, "Harness", FakeHarness)
    monkeypatch.setattr(run_eval, "HttpReplay", FakeReplay)
    monkeypatch.setenv("VOYAGER_BASE_URL", "http://example.invalid")
    monkeypatch.setenv("VOYAGER_API_KEY", "test-key")
    monkeypatch.setattr(run_eval, "build_cases", lambda _clock: [FakeCase()])
    monkeypatch.setattr(run_eval, "probe_streams", lambda _harness, _models: [])
    monkeypatch.setattr(run_eval, "RESULTS", tmp_path)

    args = SimpleNamespace(
        label="redaction",
        models=["fake-model"],
        all_models=False,
        cases=None,
        repeats=1,
        workers=1,
        hide=[],
        stream_only=False,
        offline=True,
        request_timeout=1.0,
        conversation_timeout=1.0,
        save_traces=False,
    )
    run_eval.run(args)

    rows = [
        json.loads(line)
        for path in tmp_path.glob("*.jsonl")
        if not path.name.endswith("-requests.jsonl")
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "infra_error"
    assert row["error_category"] == "timeout"
    assert row["partial"]["tool_calls"] == [{"name": "secret_tool", "error": True}]
    assert "trace" not in row
    assert "SECRET" not in json.dumps(row)


def test_a_raising_checker_is_recorded_as_a_checker_error(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from eval.cases import Trace

    class FakeHarness:
        tool_names = []

        def __init__(self, *_args, **_kwargs):
            pass

        def converse(self, _model, _prompt, **_kwargs):
            trace = Trace(prompt="prompt")
            trace.turns = 1
            trace.stop = "done"
            return trace

    class FakeReplay:
        def __init__(self, *_args, **_kwargs):
            self.recorded_at = datetime(2026, 10, 10, tzinfo=timezone.utc)

        def install(self):
            pass

        def save(self):
            pass

    class FakeCase:
        id = "fake_case"
        tags = ("short",)
        prompt = "prompt"

        def checks(self, _trace, _clock):
            raise RuntimeError("broken checker")

    monkeypatch.setattr(run_eval, "Harness", FakeHarness)
    monkeypatch.setattr(run_eval, "HttpReplay", FakeReplay)
    monkeypatch.setenv("VOYAGER_BASE_URL", "http://example.invalid")
    monkeypatch.setenv("VOYAGER_API_KEY", "test-key")
    monkeypatch.setattr(run_eval, "build_cases", lambda _clock: [FakeCase()])
    monkeypatch.setattr(run_eval, "probe_streams", lambda _harness, _models: [])
    monkeypatch.setattr(run_eval, "RESULTS", tmp_path)

    args = SimpleNamespace(
        label="checker-error",
        models=["fake-model"],
        all_models=False,
        cases=None,
        repeats=1,
        workers=1,
        hide=[],
        stream_only=False,
        offline=True,
        request_timeout=1.0,
        conversation_timeout=1.0,
        save_traces=False,
    )
    run_eval.run(args)

    rows = [
        json.loads(line)
        for path in tmp_path.glob("*.jsonl")
        if not path.name.endswith("-requests.jsonl")
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["status"] == "checker_error"
    assert rows[0]["error_category"] == "checker_error"
    assert "RuntimeError" in rows[0]["checker_exception"]


def test_rescore_fills_scored_fields_for_a_checker_error_row(tmp_path, monkeypatch):
    class FakeCase:
        id = "fake_case"

        def checks(self, _trace, _clock):
            return {"ok": True}

    monkeypatch.setattr(run_eval, "build_cases", lambda _clock: [FakeCase()])
    path = tmp_path / "rows.jsonl"
    row = {
        "label": "rescore",
        "model": "fake-model",
        "case": "fake_case",
        "status": "checker_error",
        "error_category": "checker_error",
        "checker_exception": "RuntimeError: broken",
        "trace": {
            "prompt": "prompt",
            "calls": [
                {
                    "name": "get_satellite_info",
                    "args": {},
                    "error": False,
                    "text": "{}",
                    "parsed": {"result_id": "r"},
                }
            ],
            "final": "r",
            "turns": 1,
            "stop": "done",
        },
    }
    path.write_text(json.dumps(row) + "\n")

    run_eval.rescore([path])
    rescored = json.loads(path.read_text())
    assert rescored["status"] == "ok"
    assert rescored["passed"] is True
    assert rescored["tool_errors"] == 0
    assert rescored["tool_calls"] == 1
    assert rescored["turns"] == 1
    assert rescored["stop"] == "done"
    assert rescored["reported_result_id"] is True
    run_eval.summarize([path])


def test_rescore_keeps_a_still_raising_checker_as_checker_error(tmp_path, monkeypatch):
    class FakeCase:
        id = "fake_case"

        def checks(self, _trace, _clock):
            raise RuntimeError("still broken")

    monkeypatch.setattr(run_eval, "build_cases", lambda _clock: [FakeCase()])
    path = tmp_path / "rows.jsonl"
    row = {
        "label": "rescore",
        "model": "fake-model",
        "case": "fake_case",
        "status": "ok",
        "passed": True,
        "checks": {"old": True},
        "trace": {
            "prompt": "prompt",
            "calls": [],
            "final": "",
            "turns": 0,
            "stop": "",
        },
    }
    path.write_text(json.dumps(row) + "\n")

    run_eval.rescore([path])
    rescored = json.loads(path.read_text())
    assert rescored["status"] == "checker_error"
    assert rescored["error_category"] == "checker_error"
    assert "still broken" in rescored["checker_exception"]
    assert "passed" not in rescored
    assert "checks" not in rescored


def test_run_writes_the_result_file_to_result_out(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from eval.cases import Trace

    class FakeHarness:
        tool_names = []

        def __init__(self, *_args, **_kwargs):
            pass

        def converse(self, _model, _prompt, **_kwargs):
            return Trace(prompt="prompt", turns=1, stop="answered")

    class FakeReplay:
        recorded_at = datetime(2026, 11, 21, tzinfo=timezone.utc)

        def __init__(self, *_args, **_kwargs):
            pass

        def install(self):
            pass

        def save(self):
            pass

    class FakeCase:
        id = "fake_case"
        tags = ("short",)
        prompt = "prompt"

        def checks(self, _trace, _clock):
            return {"check": True}

    monkeypatch.setattr(run_eval, "Harness", FakeHarness)
    monkeypatch.setattr(run_eval, "HttpReplay", FakeReplay)
    monkeypatch.setenv("VOYAGER_BASE_URL", "http://example.invalid")
    monkeypatch.setenv("VOYAGER_API_KEY", "test-key")
    monkeypatch.setattr(run_eval, "build_cases", lambda _clock: [FakeCase()])
    monkeypatch.setattr(run_eval, "probe_streams", lambda _harness, _models: [])
    monkeypatch.setattr(run_eval, "RESULTS", tmp_path)

    out = tmp_path / "ci-scoped.jsonl"
    args = SimpleNamespace(
        label="ci-scoped",
        models=["fake-model"],
        all_models=False,
        cases=["fake_case"],
        repeats=1,
        workers=1,
        hide=[],
        stream_only=False,
        offline=True,
        request_timeout=1.0,
        conversation_timeout=1.0,
        save_traces=False,
        result_out=out,
    )
    run_eval.run(args)

    rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert [row["case"] for row in rows] == ["fake_case"]
    assert rows[0]["passed"] is True
    assert not list(tmp_path.glob("20*.jsonl")), "no timestamped file next to it"


def test_hanging_tool_call_cannot_outlive_the_conversation_budget(monkeypatch):
    class FakeClient:
        def __init__(self, _mcp):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def call_tool(self, *_args, **_kwargs):
            await asyncio.sleep(10)

    monkeypatch.setattr(run_eval, "Client", FakeClient)
    harness = object.__new__(run_eval.Harness)
    harness.server = SimpleNamespace(mcp=object())
    started = time.monotonic()
    with pytest.raises(run_eval.ConversationTimeout):
        harness.call_tool(
            "hang", {}, deadline=time.monotonic() + 0.1
        )
    assert time.monotonic() - started < 0.8


def test_env_value_reads_a_private_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("OPENCODE_GO_API_KEY=from-file\n")
    assert (
        run_eval._env_value("OPENCODE_GO_API_KEY", [env_file])
        == "from-file"
    )


def test_voyager_credentials_prefer_the_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(run_eval, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setenv("VOYAGER_API_KEY", "from-env")
    monkeypatch.setenv("VOYAGER_BASE_URL", "http://env.invalid")
    args = SimpleNamespace(provider="voyager", base_url=None)

    base_url, key = run_eval._provider_credentials(args)

    assert key == "from-env"
    assert base_url == "http://env.invalid"


def test_voyager_credentials_fall_back_to_the_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "VOYAGER_API_KEY=from-file\nVOYAGER_BASE_URL=http://file.invalid\n"
    )
    monkeypatch.setattr(run_eval, "ENV_FILE", env_file)
    monkeypatch.delenv("VOYAGER_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGER_BASE_URL", raising=False)
    args = SimpleNamespace(provider="voyager", base_url=None)

    base_url, key = run_eval._provider_credentials(args)

    assert (base_url, key) == ("http://file.invalid", "from-file")


def test_voyager_base_url_flag_beats_the_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(run_eval, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setenv("VOYAGER_API_KEY", "from-env")
    monkeypatch.setenv("VOYAGER_BASE_URL", "http://env.invalid")
    args = SimpleNamespace(provider="voyager", base_url="http://flag.invalid")

    base_url, _key = run_eval._provider_credentials(args)

    assert base_url == "http://flag.invalid"


def test_voyager_missing_credentials_name_the_variable_and_file(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(run_eval, "ENV_FILE", tmp_path / "missing.env")
    monkeypatch.delenv("VOYAGER_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGER_BASE_URL", raising=False)
    args = SimpleNamespace(provider="voyager", base_url=None)

    with pytest.raises(ValueError, match="VOYAGER_API_KEY"):
        run_eval._provider_credentials(args)


def test_chat_sends_the_requested_reasoning_effort(monkeypatch):
    captured = {}

    def fake_completion(base_url, api_key, body, **_kwargs):
        captured["body"] = json.loads(body)
        return {"content": "ok"}

    monkeypatch.setattr(run_eval, "request_completion", fake_completion)
    harness = object.__new__(run_eval.Harness)
    harness.base_url = "http://example.invalid"
    harness.api_key = "key"
    harness.tools = []
    harness.conversation_timeout = 1
    harness.request_timeout = 1
    harness.user_agent = "test"
    harness.session_id = ""
    harness.reasoning_effort = "max"

    message = harness.chat("deepseek-v4.1-flash", [])

    assert message == {"content": "ok"}
    assert captured["body"]["reasoning_effort"] == "max"


def test_run_rejects_now_for_mixed_cases_before_any_model_work(monkeypatch):
    class FakeReplay:
        recorded_at = None

        def __init__(self, *_args, **_kwargs):
            pass

        def install(self):
            pass

    class FakeCase:
        id = "mixed_case"
        tags = ("mixed",)

    monkeypatch.setattr(run_eval, "HttpReplay", FakeReplay)
    monkeypatch.setattr(
        run_eval, "_provider_credentials", lambda _args: ("http://example", "key")
    )
    monkeypatch.setattr(run_eval, "_selected_models", lambda _args: ["model"])
    monkeypatch.setattr(run_eval, "build_cases", lambda _clock: [FakeCase()])

    def unexpected(*_args, **_kwargs):
        raise AssertionError("model work started before the clock guard")

    monkeypatch.setattr(run_eval, "Harness", unexpected)
    monkeypatch.setattr(run_eval, "probe_streams", unexpected)

    args = SimpleNamespace(
        now="2026-11-20T00:00:00+00:00",
        provider="opencode-go",
        reasoning_effort="max",
        cases=None,
        models=["model"],
        all_models=False,
        api_key_env=None,
        base_url=None,
        hide=[],
        offline=True,
    )
    with pytest.raises(ValueError, match="Mixed cases use the recorded"):
        run_eval.run(args)
