"""Offline HTTP-seam checks for the bounded tool-calling eval harness."""

import asyncio
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from eval import run_eval


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
    monkeypatch.setattr(run_eval, "load_env", lambda _path: {
        "VOYAGER_BASE_URL": "http://example.invalid",
        "VOYAGER_API_KEY": "test-key",
    })
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
