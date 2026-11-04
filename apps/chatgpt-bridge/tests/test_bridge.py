import json
import os
import stat
import threading
import time
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

import bridge
from bridge import BridgeError, Connection


def tool(name="search"):
    return {"type": "function", "function": {"name": name, "description": "d", "parameters": {"type": "object"}}}


def test_request_uses_only_fields_the_plan_route_accepts():
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.2,
        "max_tokens": 5,
        "user": "u",
        "metadata": {},
    }
    request = bridge.to_responses(body)
    assert request == {
        "model": "m",
        "input": [{"role": "user", "content": "hi"}],
        "store": False,
        "stream": True,
    }


def test_system_messages_become_instructions():
    request = bridge.to_responses({"model": "m", "messages": [
        {"role": "system", "content": "a"},
        {"role": "developer", "content": [{"type": "text", "text": "b"}]},
        {"role": "user", "content": "hi"},
    ]})
    assert request["instructions"] == "a\n\nb"
    assert all(item.get("role") != "system" for item in request["input"])


def test_tools_are_grouped_in_a_namespace():
    request = bridge.to_responses({"model": "m", "messages": [], "tools": [tool("a"), tool("b")], "tool_choice": "auto"})
    assert request["tool_choice"] == "auto"
    [namespace] = request["tools"]
    assert namespace["type"] == "namespace"
    assert [t["name"] for t in namespace["tools"]] == ["a", "b"]
    assert all(t["type"] == "function" for t in namespace["tools"])


def test_tool_round_trip_maps_to_call_items():
    request = bridge.to_responses({"model": "m", "messages": [
        {"role": "assistant", "content": "looking", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
    ]})
    assert request["input"] == [
        {"role": "assistant", "content": "looking"},
        {"type": "function_call", "call_id": "c1", "name": "a", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "result"},
    ]


def test_images_are_forwarded_and_unknown_parts_are_rejected():
    request = bridge.to_responses({"model": "m", "messages": [{"role": "user", "content": [
        {"type": "text", "text": "see"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}},
    ]}]})
    assert request["input"][0]["content"] == [
        {"type": "input_text", "text": "see"},
        {"type": "input_image", "image_url": "data:image/png;base64,AA"},
    ]
    with pytest.raises(BridgeError) as error:
        bridge.to_responses({"model": "m", "messages": [{"role": "user", "content": [{"type": "input_audio"}]}]})
    assert error.value.status == 400
    assert "input_audio" in error.value.message


def test_stream_starts_with_role_and_carries_text_and_tool_calls():
    events = [
        {"type": "response.output_text.delta", "delta": "he"},
        {"type": "response.output_item.added", "item": {"type": "function_call", "id": "i1", "call_id": "c1", "name": "a"}},
        {"type": "response.function_call_arguments.delta", "item_id": "i1", "delta": '{"x"'},
        {"type": "response.function_call_arguments.delta", "item_id": "i1", "delta": ":1}"},
        {"type": "response.completed", "response": {"usage": {"input_tokens": 3, "output_tokens": 4}}},
    ]
    chunks = list(bridge.chat_chunks(events, "m"))
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    assert chunks[1]["choices"][0]["delta"] == {"content": "he"}
    call = chunks[2]["choices"][0]["delta"]["tool_calls"][0]
    assert (call["index"], call["id"], call["function"]["name"]) == (0, "c1", "a")
    assert chunks[3]["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"] == '{"x"'
    assert chunks[-2]["choices"][0]["finish_reason"] == "tool_calls"
    assert chunks[-1]["usage"] == {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}


def test_arguments_sent_only_when_done_are_not_lost_or_doubled():
    events = [
        {"type": "response.output_item.added", "item": {"type": "function_call", "id": "i1", "call_id": "c1", "name": "a"}},
        {"type": "response.function_call_arguments.done", "item_id": "i1", "arguments": "{}"},
        {"type": "response.output_item.added", "item": {"type": "function_call", "id": "i2", "call_id": "c2", "name": "b"}},
        {"type": "response.function_call_arguments.delta", "item_id": "i2", "delta": "{}"},
        {"type": "response.function_call_arguments.done", "item_id": "i2", "arguments": "{}"},
        {"type": "response.completed", "response": {}},
    ]
    message = bridge.chat_completion(events, "m")["choices"][0]["message"]
    assert [c["function"]["arguments"] for c in message["tool_calls"]] == ["{}", "{}"]


def test_non_streaming_reply_is_assembled():
    events = [
        {"type": "response.output_text.delta", "delta": "he"},
        {"type": "response.output_text.delta", "delta": "llo"},
        {"type": "response.completed", "response": {"usage": {"input_tokens": 1, "output_tokens": 1}}},
    ]
    reply = bridge.chat_completion(events, "m")
    assert reply["choices"][0]["message"] == {"role": "assistant", "content": "hello"}
    assert reply["choices"][0]["finish_reason"] == "stop"
    assert reply["usage"]["total_tokens"] == 2


@pytest.mark.parametrize("event,code", [
    ({"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "x"}}},
     "subscription_sharing_usage_limit_exceeded"),
    ({"type": "error", "code": "server_error", "message": "boom"}, "server_error"),
    ({"type": "response.incomplete", "response": {"incomplete_details": {"reason": "content_filter"}}}, "response_incomplete"),
])
def test_failures_in_the_stream_raise(event, code):
    with pytest.raises(BridgeError) as error:
        list(bridge.chat_chunks([{"type": "response.output_text.delta", "delta": "a"}, event], "m"))
    assert error.value.code == code


def test_stream_ending_without_completion_is_an_error():
    with pytest.raises(BridgeError) as error:
        list(bridge.chat_chunks([{"type": "response.output_text.delta", "delta": "a"}], "m"))
    assert error.value.code == "stream_interrupted"


def test_usage_limit_error_links_to_usage_settings_without_guessing_a_reset():
    error = bridge.upstream_error({"error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "x"}}, 429, "req_1")
    assert error.status == 429
    assert bridge.USAGE_URL in error.message
    assert "req_1" in error.message


def test_unknown_upstream_error_keeps_its_message_and_code():
    error = bridge.upstream_error({"error": {"code": "model_not_found", "message": "no such model"}}, 404, None)
    assert (error.status, error.code) == (404, "model_not_found")
    assert "no such model" in error.message


def connected(tmp_path, **overrides):
    connection = Connection(tmp_path / "connection.json")
    connection.state.update({
        "client_id": "app_1",
        "subject": "sub_1",
        "email": "a@example.com",
        "access_token": "old",
        "refresh_token": "r1",
        "expires_at": time.time() + 3600,
        "scopes": bridge.SCOPES.split(),
        "key": "bridge-key",
        **overrides,
    })
    return connection


class FakeResponse:
    def __init__(self, status, data):
        self.status_code = status
        self.data = data
        self.headers = {"x-request-id": "req_2"}
        self.ok = status < 400

    def json(self):
        return self.data


def test_state_file_is_owner_only_and_survives_a_restart(tmp_path):
    connection = connected(tmp_path)
    connection.save()
    assert stat.S_IMODE(os.stat(connection.path).st_mode) == 0o600
    assert Connection(connection.path).state["refresh_token"] == "r1"


def test_host_id_is_created_once_and_never_identifies_the_user(tmp_path):
    first = Connection(tmp_path / "connection.json")
    second = Connection(tmp_path / "connection.json")
    assert first.state["host_id"] == second.state["host_id"]


def test_valid_token_is_used_without_refreshing(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge.requests, "post", lambda *a, **k: pytest.fail("should not refresh"))
    assert connected(tmp_path).access_token() == "old"


def test_expired_token_refreshes_and_saves_the_rotated_grant(tmp_path, monkeypatch):
    connection = connected(tmp_path, expires_at=0)
    monkeypatch.setattr(bridge, "discovery", lambda: {"token_endpoint": "https://t"})
    monkeypatch.setattr(bridge.requests, "post", lambda *a, **k: FakeResponse(200, {
        "access_token": "new", "refresh_token": "r2", "expires_in": 3600, "token_type": "Bearer",
    }))
    assert connection.access_token() == "new"
    saved = json.loads(connection.path.read_text())
    assert (saved["access_token"], saved["refresh_token"]) == ("new", "r2")


def test_terminal_refresh_failure_clears_tokens_and_asks_for_sign_in(tmp_path, monkeypatch):
    connection = connected(tmp_path, expires_at=0)
    monkeypatch.setattr(bridge, "discovery", lambda: {"token_endpoint": "https://t"})
    monkeypatch.setattr(bridge.requests, "post", lambda *a, **k: FakeResponse(400, {"error": "invalid_grant"}))
    with pytest.raises(BridgeError) as error:
        connection.access_token()
    assert error.value.status == 401
    assert bridge.PAGE_URL in error.value.message
    assert "refresh_token" not in connection.state
    assert connection.state["client_id"] == "app_1"


def test_temporary_refresh_failure_keeps_credentials(tmp_path, monkeypatch):
    connection = connected(tmp_path, expires_at=0)
    monkeypatch.setattr(bridge, "discovery", lambda: {"token_endpoint": "https://t"})
    monkeypatch.setattr(bridge.requests, "post", lambda *a, **k: FakeResponse(503, {"error": "unavailable"}))
    with pytest.raises(BridgeError) as error:
        connection.access_token()
    assert error.value.status == 503
    assert connection.state["refresh_token"] == "r1"


def test_missing_plan_scope_is_reported_instead_of_calling_openai(tmp_path):
    connection = connected(tmp_path, scopes=["openid", "email"])
    with pytest.raises(BridgeError) as error:
        connection.access_token()
    assert error.value.code == "plan_usage_not_enabled"


def test_authorization_url_registers_once_then_reuses_the_client(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "discovery", lambda: {"authorization_endpoint": "https://a/authorize"})
    first = Connection(tmp_path / "first.json").begin_sign_in()
    assert "client_id=dynamic_agent_client" in first
    assert "agent_name_hint=TATC+AI" in first
    assert "ext_agent_host_id=" in first
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8788%2Fauth%2Fcallback" in first
    assert "code_challenge_method=S256" in first

    returning = connected(tmp_path).begin_sign_in()
    assert "client_id=app_1" in returning
    assert "agent_name_hint" not in returning


def test_callback_with_wrong_state_does_not_consume_the_sign_in(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "discovery", lambda: {"authorization_endpoint": "https://a"})
    connection = Connection(tmp_path / "connection.json")
    connection.begin_sign_in()
    with pytest.raises(BridgeError) as error:
        connection.finish_sign_in({"state": ["wrong"], "code": ["c"], "client_id": ["app_1"]})
    assert error.value.code == "invalid_state"
    assert connection.pending is not None


def test_sign_in_for_a_different_account_is_refused(tmp_path, monkeypatch):
    connection = connected(tmp_path)
    monkeypatch.setattr(bridge, "discovery", lambda: {"authorization_endpoint": "https://a", "token_endpoint": "https://t"})
    connection.begin_sign_in()
    monkeypatch.setattr(bridge.requests, "post", lambda *a, **k: FakeResponse(200, {
        "access_token": "x", "refresh_token": "y", "expires_in": 1, "token_type": "Bearer", "id_token": "i", "scope": bridge.SCOPES,
    }))
    monkeypatch.setattr(bridge, "verify_identity", lambda *a: {"sub": "someone_else", "email": "b@example.com"})
    with pytest.raises(BridgeError) as error:
        connection.finish_sign_in({"state": [connection.pending["state"]], "code": ["c"], "client_id": ["app_1"]})
    assert error.value.code == "account_mismatch"
    assert connection.state["access_token"] == "old"


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "connection", connected(tmp_path))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()


def call(port, method, path, headers=None, body=None):
    client = HTTPConnection("127.0.0.1", port, timeout=5)
    client.request(method, path, body=body, headers=headers or {})
    response = client.getresponse()
    return response.status, response.read().decode()


def test_api_rejects_a_missing_or_wrong_key(server):
    assert call(server, "GET", "/v1/models")[0] == 401
    status, text = call(server, "GET", "/v1/models", {"Authorization": "Bearer wrong"})
    assert status == 401
    assert bridge.PAGE_URL in text


def test_api_works_with_the_key(server, monkeypatch):
    monkeypatch.setattr(bridge.requests, "get", lambda *a, **k: FakeResponse(200, {"models": [
        {"slug": "shown", "display_name": "Shown", "visibility": "list"},
        {"slug": "hidden", "display_name": "Hidden", "visibility": "hide"},
    ]}))
    status, text = call(server, "GET", "/v1/models", {"Authorization": "Bearer bridge-key"})
    assert status == 200
    assert [m["id"] for m in json.loads(text)["data"]] == ["shown"]


def test_failures_tell_the_openai_client_not_to_retry(server):
    client = HTTPConnection("127.0.0.1", server, timeout=5)
    client.request("GET", "/v1/models")
    assert client.getresponse().getheader("x-should-retry") == "false"


def test_status_page_is_only_served_on_the_loopback_host(server):
    assert call(server, "GET", "/", {"Host": "evil.example"})[0] == 404
    assert call(server, "GET", "/auth/callback?state=x", {"Host": "chatgpt-bridge:8788"})[0] == 404


def test_cross_site_posts_are_refused(server, monkeypatch):
    monkeypatch.setattr(bridge, "HOST", f"127.0.0.1:{server}")
    headers = {"Host": f"127.0.0.1:{server}", "Origin": "https://evil.example"}
    assert call(server, "POST", "/disconnect", headers)[0] == 403
    assert bridge.connection.state["access_token"] == "old"


def test_page_shows_key_account_and_plan_usage(server, monkeypatch):
    monkeypatch.setattr(bridge, "HOST", f"127.0.0.1:{server}")
    status, page = call(server, "GET", "/", {"Host": f"127.0.0.1:{server}"})
    assert status == 200
    for text in ("bridge-key", "a@example.com", "Using ChatGPT plan", "Manage usage", bridge.USAGE_URL):
        assert text in page


def test_page_offers_sign_in_when_not_connected(server, monkeypatch):
    monkeypatch.setattr(bridge, "HOST", f"127.0.0.1:{server}")
    bridge.connection.state.pop("access_token")
    bridge.connection.state.pop("refresh_token")
    _, page = call(server, "GET", "/", {"Host": f"127.0.0.1:{server}"})
    assert "Continue with ChatGPT" in page
    assert "bridge-key" not in page


def test_request_logs_never_contain_the_query_string(server, monkeypatch, capsys):
    monkeypatch.setattr(bridge, "HOST", f"127.0.0.1:{server}")
    call(server, "GET", "/auth/callback?code=SECRETCODE&state=SECRETSTATE", {"Host": f"127.0.0.1:{server}"})
    assert "SECRET" not in capsys.readouterr().err


@pytest.mark.parametrize("status,code", [
    (429, "subscription_sharing_usage_limit_exceeded"),
    (503, "subscription_sharing_usage_unavailable"),
    (500, "server_error"),
])
def test_retryable_failures_reach_the_person_instead_of_being_retried(server, monkeypatch, status, code):
    class Upstream(FakeResponse):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(bridge.requests, "post", lambda *a, **k: Upstream(status, {"error": {"code": code, "message": "m"}}))
    got, text = call(server, "POST", "/v1/chat/completions",
                     {"Authorization": "Bearer bridge-key", "Content-Type": "application/json"},
                     json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}], "stream": True}))
    assert got == bridge.FINAL_FAILURE_STATUS
    assert code in json.loads(text)["error"]["message"]


def test_token_response_without_a_refresh_token_is_reported(tmp_path):
    connection = connected(tmp_path)
    with pytest.raises(BridgeError) as error:
        connection.store_tokens({"access_token": "a", "expires_in": 1})
    assert error.value.code == "invalid_token_response"
    assert connection.state["access_token"] == "old"


def test_malformed_request_is_reported(server):
    got, text = call(server, "POST", "/v1/chat/completions",
                     {"Authorization": "Bearer bridge-key", "Content-Type": "application/json"},
                     json.dumps({"model": "m", "messages": [{"role": "assistant", "tool_calls": [{}]}]}))
    assert got == 400
    assert json.loads(text)["error"]["code"] == "invalid_request"


def test_unexpected_stream_shape_becomes_an_error_event(server, monkeypatch):
    class Upstream(FakeResponse):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_lines(self):
            yield b'data: {"type": "response.function_call_arguments.delta", "item_id": "unknown", "delta": "x"}'

    monkeypatch.setattr(bridge.requests, "post", lambda *a, **k: Upstream(200, {}))
    got, text = call(server, "POST", "/v1/chat/completions",
                     {"Authorization": "Bearer bridge-key", "Content-Type": "application/json"},
                     json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}], "stream": True}))
    assert got == 200
    assert '"code": "invalid_stream"' in text
