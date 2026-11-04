"""Lets LibreChat use a person's ChatGPT plan through Sign in with ChatGPT.

Implements OpenAI's documented flow for locally hosted open-source apps:
https://developers.openai.com/siwc/token-sharing-open-source

LibreChat speaks Chat Completions, while the plan route only takes streaming
Responses requests with a restricted set of fields, so this service holds the
OAuth tokens and translates between the two. Failures are returned as errors;
requests are never retried elsewhere, so nothing moves to another provider.
"""

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
import requests

ISSUER = "https://auth.openai.com"
API = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
APP_NAME = "TATC AI"
PORT = 8788
HOST = f"127.0.0.1:{PORT}"
PAGE_URL = f"http://{HOST}"
REDIRECT_URI = f"{PAGE_URL}/auth/callback"
USAGE_URL = "https://chatgpt.com/settings/usage"
STATE_PATH = Path("/data/connection.json")
# LibreChat's client silently retries 429 and 5xx for minutes, leaving an empty
# reply, and OpenAI asks apps to pause at a usage limit. LangChain does not retry
# 409 and the OpenAI SDK honors x-should-retry, so failures reach the person at
# once; the real code stays in the message.
FINAL_FAILURE_STATUS = 409
TIMEOUT = (15, 180)
TERMINAL_REFRESH_ERRORS = {
    "invalid_grant", "invalid_refresh_token", "token_expired",
    "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused",
}


class BridgeError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def upstream_error(detail, status, request_id):
    """Build the error a person sees from an OpenAI error body or stream event."""
    error = detail.get("error", detail) if isinstance(detail, dict) else {}
    if isinstance(error, str):
        error = {"code": error}
    code = str(error.get("code") or error.get("type") or "upstream_error").replace("_v2_", "_")
    message = error.get("message") or f"OpenAI returned HTTP {status}."
    if code == "subscription_sharing_usage_limit_exceeded":
        message = f"The ChatGPT plan usage limit for this app has been reached. Manage usage: {USAGE_URL}"
    elif code == "subscription_sharing_user_not_eligible":
        message = "This ChatGPT account is not eligible to use its plan in other apps. Use another provider's API key."
    elif code == "subscription_sharing_invalid_user":
        message = f"ChatGPT did not accept this connection. Sign in again at {PAGE_URL}."
    elif code == "subscription_sharing_usage_unavailable":
        message = "ChatGPT plan usage is temporarily unavailable. Try again later."
    suffix = f" (request {request_id})" if request_id else ""
    return BridgeError(status, code, f"{message} [{code}]{suffix}")


def json_body(response):
    try:
        return response.json()
    except ValueError:
        return {}


# Chat Completions to Responses


def text_of(content):
    if isinstance(content, str):
        return content
    return "".join(part["text"] for part in content_parts(content or []))


def content_parts(content):
    parts = []
    for part in content:
        if part.get("type") == "text":
            parts.append({"type": "input_text", "text": part["text"]})
        elif part.get("type") == "image_url":
            parts.append({"type": "input_image", "image_url": part["image_url"]["url"]})
        else:
            raise BridgeError(400, "unsupported_content", f"ChatGPT plan requests cannot include {part.get('type')} content.")
    return parts


def to_responses(body):
    instructions, items = [], []
    for message in body.get("messages", []):
        role, content = message.get("role"), message.get("content")
        if role in ("system", "developer"):
            instructions.append(text_of(content))
        elif role == "user":
            items.append({"role": "user", "content": content if isinstance(content, str) else content_parts(content)})
        elif role == "assistant":
            text = text_of(content)
            if text:
                items.append({"role": "assistant", "content": text})
            for call in message.get("tool_calls") or []:
                items.append({
                    "type": "function_call",
                    "call_id": call["id"],
                    "name": call["function"]["name"],
                    "arguments": call["function"]["arguments"],
                })
        elif role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": message["tool_call_id"],
                "output": content if isinstance(content, str) else content_parts(content),
            })
        else:
            raise BridgeError(400, "unsupported_message", f"ChatGPT plan requests cannot include {role} messages.")

    # Other request fields, such as temperature, are rejected by the plan route.
    request = {"model": body.get("model"), "input": items, "store": False, "stream": True}
    if instructions:
        request["instructions"] = "\n\n".join(instructions)
    if body.get("tools"):
        request["tools"] = [{
            "type": "namespace",
            "name": "tools",
            "description": "Tools available in this conversation.",
            "tools": [response_tool(tool) for tool in body["tools"]],
        }]
    if "tool_choice" in body:
        choice = body["tool_choice"]
        request["tool_choice"] = choice if isinstance(choice, str) else {"type": "function", "name": choice["function"]["name"]}
    if "parallel_tool_calls" in body:
        request["parallel_tool_calls"] = body["parallel_tool_calls"]
    return request


def response_tool(tool):
    if tool.get("type") != "function":
        raise BridgeError(400, "unsupported_tool", f"ChatGPT plan requests cannot use {tool.get('type')} tools.")
    function = tool["function"]
    return {
        "type": "function",
        "name": function["name"],
        "description": function.get("description", ""),
        "parameters": function.get("parameters", {"type": "object", "properties": {}}),
    }


# Responses to Chat Completions


def chat_chunks(events, model):
    base = {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
    }

    def chunk(delta, finish=None):
        return {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    def arguments(index, text):
        return chunk({"tool_calls": [{"index": index, "function": {"arguments": text}}]})

    # LibreChat's client drops streamed tool calls unless the first delta has a role.
    yield chunk({"role": "assistant", "content": ""})
    calls = {}
    for event in events:
        kind = event.get("type")
        if kind == "response.output_text.delta":
            yield chunk({"content": event["delta"]})
        elif kind == "response.output_item.added" and event["item"].get("type") == "function_call":
            item = event["item"]
            calls[item["id"]] = {"index": len(calls), "streamed": False}
            yield chunk({"tool_calls": [{
                "index": calls[item["id"]]["index"],
                "id": item["call_id"],
                "type": "function",
                "function": {"name": item["name"], "arguments": ""},
            }]})
        elif kind == "response.function_call_arguments.delta":
            call = calls[event["item_id"]]
            call["streamed"] = True
            yield arguments(call["index"], event["delta"])
        elif kind == "response.function_call_arguments.done":
            call = calls[event["item_id"]]
            if not call["streamed"]:
                yield arguments(call["index"], event["arguments"])
        elif kind == "response.completed":
            yield chunk({}, "tool_calls" if calls else "stop")
            usage = event["response"].get("usage")
            if usage:
                yield {**base, "choices": [], "usage": {
                    "prompt_tokens": usage["input_tokens"],
                    "completion_tokens": usage["output_tokens"],
                    "total_tokens": usage["input_tokens"] + usage["output_tokens"],
                }}
            return
        elif kind == "response.failed":
            raise upstream_error(event["response"], 502, event["response"].get("id"))
        elif kind == "error":
            raise upstream_error(event, 502, None)
        elif kind == "response.incomplete":
            reason = (event["response"].get("incomplete_details") or {}).get("reason", "unknown")
            raise BridgeError(502, "response_incomplete", f"ChatGPT stopped before completing the response ({reason}).")
    raise BridgeError(502, "stream_interrupted", "The connection to ChatGPT ended before the response completed.")


def chat_completion(events, model):
    text, calls, finish, usage, reply_id, created = "", {}, "stop", None, None, None
    for chunk in chat_chunks(events, model):
        reply_id, created = chunk["id"], chunk["created"]
        usage = chunk.get("usage") or usage
        for choice in chunk["choices"]:
            finish = choice["finish_reason"] or finish
            text += choice["delta"].get("content") or ""
            for call in choice["delta"].get("tool_calls", []):
                saved = calls.setdefault(call["index"], {"function": {"arguments": ""}})
                saved.update({k: v for k, v in call.items() if k in ("id", "type")})
                saved["function"]["name"] = call["function"].get("name") or saved["function"].get("name")
                saved["function"]["arguments"] += call["function"]["arguments"]
    message = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = [calls[index] for index in sorted(calls)]
    reply = {
        "id": reply_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    if usage:
        reply["usage"] = usage
    return reply


def response_events(response):
    for line in response.iter_lines():
        if line.startswith(b"data: ") and line != b"data: [DONE]":
            try:
                yield json.loads(line[6:])
            except ValueError:
                raise BridgeError(502, "invalid_stream", "ChatGPT sent a response that could not be read.")


# Sign-in and tokens

_discovery = None


def discovery():
    global _discovery
    if _discovery is None:
        try:
            response = requests.get(f"{ISSUER}/.well-known/openid-configuration", timeout=TIMEOUT)
            data = response.json()
        except (requests.RequestException, ValueError):
            raise BridgeError(503, "discovery_failed", "Could not reach ChatGPT sign-in. Check your connection and try again.")
        if data.get("issuer") != ISSUER:
            raise BridgeError(503, "discovery_failed", "ChatGPT sign-in configuration could not be verified.")
        _discovery = data
    return _discovery


def verify_identity(id_token, client_id, nonce):
    try:
        key = jwt.PyJWKClient(discovery()["jwks_uri"]).get_signing_key_from_jwt(id_token)
    except jwt.PyJWKClientConnectionError:
        raise BridgeError(503, "identity_verification_unavailable", "ChatGPT identity verification is unavailable. Try again shortly.")
    except jwt.PyJWTError:
        raise BridgeError(401, "invalid_id_token", "The ChatGPT identity could not be verified. Sign in again.")
    try:
        claims = jwt.decode(
            id_token, key, algorithms=["RS256"], audience=client_id, issuer=ISSUER, leeway=5,
            options={"require": ["iss", "aud", "exp", "iat", "sub"]},
        )
    except jwt.PyJWTError:
        raise BridgeError(401, "invalid_id_token", "The ChatGPT identity could not be verified. Sign in again.")
    if nonce is not None and claims.get("nonce") != nonce:
        raise BridgeError(401, "invalid_id_token", "The ChatGPT identity could not be verified. Sign in again.")
    return claims


class Connection:
    """The one ChatGPT sign-in this install holds, saved as an owner-only file."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.pending = None
        self.message = None
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {}
        if "host_id" not in self.state:
            self.state["host_id"] = str(uuid.uuid4())
            self.save()

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(self.state, handle)
        os.replace(temporary, self.path)

    @property
    def connected(self):
        return "refresh_token" in self.state

    @property
    def plan_enabled(self):
        return PLAN_SCOPE in self.state.get("scopes", [])

    def begin_sign_in(self):
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        client_id = self.state.get("client_id")
        query = {
            "client_id": client_id or "dynamic_agent_client",
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "resource": API,
            "state": state,
            "nonce": nonce,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "ext_agent_host_id": self.state["host_id"],
        }
        if not client_id:
            query["agent_name_hint"] = APP_NAME
        url = f"{discovery()['authorization_endpoint']}?{urlencode(query)}"
        self.pending = {"state": state, "nonce": nonce, "verifier": verifier}
        return url

    def finish_sign_in(self, query):
        pending = self.pending
        returned = query.get("state", [""])[0]
        if not pending or not hmac.compare_digest(returned.encode(), pending["state"].encode()):
            raise BridgeError(400, "invalid_state", "This sign-in link is out of date. Start again from this page.")
        self.pending = None
        if "error" in query:
            raise BridgeError(400, query["error"][0], f"ChatGPT sign-in was not completed ({query['error'][0]}).")
        client_id = query.get("client_id", [self.state.get("client_id")])[0]
        if not client_id or client_id == "dynamic_agent_client" or "code" not in query:
            raise BridgeError(400, "registration_incomplete", "ChatGPT did not complete app registration. Try signing in again.")
        with self.lock:
            # A one-time code can fail after registration, so keep the issued client.
            self.state["client_id"] = client_id
            self.save()
            data = self.token_request({
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": query["code"][0],
                "code_verifier": pending["verifier"],
                "redirect_uri": REDIRECT_URI,
                "resource": API,
            })
            claims = verify_identity(data.get("id_token", ""), client_id, pending["nonce"])
            if self.state.get("subject") not in (None, claims["sub"]):
                raise BridgeError(409, "account_mismatch", "This is a different ChatGPT account. Disconnect the current one first.")
            self.state.update({"subject": claims["sub"], "email": claims.get("email")})
            self.state.setdefault("key", f"tatc-{secrets.token_urlsafe(32)}")
            self.store_tokens(data)

    def token_request(self, fields):
        endpoint = discovery()["token_endpoint"]
        try:
            response = requests.post(endpoint, data=fields, timeout=TIMEOUT, allow_redirects=False)
        except requests.RequestException:
            raise BridgeError(503, "network_error", "Could not reach ChatGPT. Check your connection and try again.")
        data = json_body(response)
        if response.status_code != 200:
            raise upstream_error(data, response.status_code, response.headers.get("x-request-id"))
        return data

    def store_tokens(self, data):
        if not all(data.get(field) for field in ("access_token", "refresh_token", "expires_in")):
            raise BridgeError(502, "invalid_token_response", "ChatGPT returned incomplete credentials. Sign in again.")
        self.state.update({
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": time.time() + data["expires_in"],
            "scopes": data["scope"].split() if data.get("scope") else self.state["scopes"],
        })
        self.save()

    def access_token(self):
        with self.lock:
            if not self.connected:
                raise BridgeError(401, "not_signed_in", f"ChatGPT is not connected. Sign in at {PAGE_URL}.")
            if not self.plan_enabled:
                raise BridgeError(403, "plan_usage_not_enabled",
                                  f"ChatGPT plan usage is not enabled for this sign-in. Enable it at {PAGE_URL}, or use another provider's API key.")
            if self.state["expires_at"] - 60 > time.time():
                return self.state["access_token"]
            try:
                data = self.token_request({
                    "grant_type": "refresh_token",
                    "client_id": self.state["client_id"],
                    "refresh_token": self.state["refresh_token"],
                    "resource": API,
                })
            except BridgeError as error:
                if error.code not in TERMINAL_REFRESH_ERRORS:
                    raise BridgeError(503, error.code, f"ChatGPT could not renew this connection right now: {error.message}")
                for field in ("access_token", "refresh_token", "expires_at"):
                    self.state.pop(field, None)
                self.save()
                raise BridgeError(401, error.code, f"Your ChatGPT connection has expired. Sign in again at {PAGE_URL}.")
            if data.get("id_token") and verify_identity(data["id_token"], self.state["client_id"], None)["sub"] != self.state["subject"]:
                raise BridgeError(409, "account_mismatch", f"The renewed ChatGPT identity changed. Sign in again at {PAGE_URL}.")
            self.store_tokens(data)
            return self.state["access_token"]

    def disconnect(self):
        with self.lock:
            token, client_id = self.state.get("refresh_token"), self.state.get("client_id")
            self.state = {"host_id": self.state["host_id"]}
            self.save()
        if not token:
            return
        try:
            response = requests.post(
                discovery()["revocation_endpoint"],
                data={"token": token, "token_type_hint": "refresh_token", "client_id": client_id},
                timeout=TIMEOUT,
            )
            confirmed = response.status_code == 200
        except (BridgeError, requests.RequestException):
            confirmed = False
        if not confirmed:
            raise BridgeError(502, "revocation_failed", f"Removed from this computer, but ChatGPT did not confirm it. Disconnect the app in {USAGE_URL}.")


connection = None


def render_page():
    state = connection.state
    notice = f'<p class="error" role="alert">{html.escape(connection.message)}</p>' if connection.message else ""
    connection.message = None
    if connection.connected and connection.plan_enabled:
        body = f"""
        <p>Connected as <b>{html.escape(state.get("email") or "your ChatGPT account")}</b>.</p>
        <p class="plan">Using ChatGPT plan · <a href="{USAGE_URL}" target="_blank" rel="noreferrer">Manage usage</a></p>
        <h2>Finish in TATC AI</h2>
        <ol><li>Open Settings, then Data &amp; Privacy, then Provider API keys.</li>
        <li>Choose <b>ChatGPT Plan</b> and paste this key:</li></ol>
        <input readonly aria-label="Connection key" value="{html.escape(state["key"])}">
        <p>Requests with this key count toward your plan limits. Keep it private; anyone who has it can use your plan through this computer.</p>
        <form method="post" action="/disconnect"><button class="secondary">Disconnect</button></form>"""
    elif connection.connected:
        body = """
        <p>You signed in, but ChatGPT plan usage was not enabled. Enable it to use your plan here, or add another provider's API key in TATC AI.</p>
        <form method="post" action="/connect"><button>Continue with ChatGPT</button></form>
        <form method="post" action="/disconnect"><button class="secondary">Disconnect</button></form>"""
    else:
        body = """
        <p>Use your ChatGPT Plus or Pro plan for TATC AI on this computer. Usage counts toward your existing plan limits; there is no API key to set up.</p>
        <form method="post" action="/connect"><button>Continue with ChatGPT</button></form>"""
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
    <title>{APP_NAME} · ChatGPT plan</title>
    <style>body{{font:16px/1.5 system-ui;max-width:34rem;margin:10vh auto;padding:0 16px;color:#202123}}
    h1{{font-size:24px}}h2{{font-size:18px}}input{{width:100%;box-sizing:border-box;padding:8px;font:13px monospace}}
    button{{margin-top:12px;padding:10px 16px;font:inherit;background:#202123;color:#fff;border:1px solid #202123;border-radius:6px;cursor:pointer}}
    button.secondary{{background:#fff;color:#202123}}.error{{color:#b00020}}.plan{{font-weight:600}}</style>
    <h1>{APP_NAME} · ChatGPT plan</h1>{notice}{body}</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # The sign-in callback carries its one-time code in the query string.
        path = urlsplit(self.path).path
        print(f"{self.command} {path} {args[1] if len(args) > 1 else ''}", file=sys.stderr)

    def do_GET(self):
        route = urlsplit(self.path)
        if route.path == "/v1/models":
            self.api(self.models)
        elif not self.on_loopback():
            self.send_error(404)
        elif route.path == "/":
            self.send_page()
        elif route.path == "/auth/callback":
            try:
                connection.finish_sign_in(parse_qs(route.query))
            except BridgeError as error:
                connection.message = error.message
            self.redirect("/")
        else:
            self.send_error(404)

    def do_POST(self):
        route = urlsplit(self.path).path
        if route == "/v1/chat/completions":
            self.api(self.chat)
        elif not self.on_loopback():
            self.send_error(404)
        elif self.headers.get("Origin") != f"http://{HOST}":
            self.send_error(403)
        elif route == "/connect":
            try:
                self.redirect(connection.begin_sign_in())
            except BridgeError as error:
                connection.message = error.message
                self.redirect("/")
        elif route == "/disconnect":
            try:
                connection.disconnect()
            except BridgeError as error:
                connection.message = error.message
            self.redirect("/")
        else:
            self.send_error(404)

    def on_loopback(self):
        # Stops other websites reaching this page through DNS rebinding.
        return self.headers.get("Host") == HOST

    def send_page(self):
        page = render_page().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         f"default-src 'none'; style-src 'unsafe-inline'; form-action 'self' {ISSUER}; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(page)

    def redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

    def send_json(self, status, data):
        payload = json.dumps(data).encode()
        self.send_response(status)
        if status >= 400:
            self.send_header("x-should-retry", "false")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_failure(self, error):
        status = FINAL_FAILURE_STATUS if error.status == 429 or error.status >= 500 else error.status
        self.send_json(status, {"error": {"message": error.message, "code": error.code, "type": "bridge_error"}})

    def api(self, action):
        try:
            key = connection.state.get("key", "")
            supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
            if not key or not hmac.compare_digest(supplied.encode(), key.encode()):
                raise BridgeError(401, "invalid_connection_key", f"This connection key is not valid. Get the current one at {PAGE_URL}.")
            action()
        except BridgeError as error:
            self.send_failure(error)
        except (KeyError, TypeError, ValueError, AttributeError):
            self.send_failure(BridgeError(400, "invalid_request", "The bridge could not read this request."))

    def models(self):
        try:
            response = requests.get(f"{API}/models", headers={"Authorization": f"Bearer {connection.access_token()}"},
                                    timeout=TIMEOUT, allow_redirects=False)
        except requests.RequestException:
            raise BridgeError(503, "network_error", "Could not reach ChatGPT. Check your connection and try again.")
        data = json_body(response)
        if response.status_code != 200:
            raise upstream_error(data, response.status_code, response.headers.get("x-request-id"))
        self.send_json(200, {"object": "list", "data": [
            {"id": model["slug"], "object": "model", "owned_by": "openai"}
            for model in data["models"] if model.get("visibility") == "list"
        ]})

    def chat(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        request = to_responses(body)
        token = connection.access_token()
        try:
            response = requests.post(
                f"{API}/responses", json=request, stream=True, timeout=TIMEOUT, allow_redirects=False,
                headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
            )
        except requests.RequestException:
            raise BridgeError(503, "network_error", "Could not reach ChatGPT. Check your connection and try again.")
        with response:
            if response.status_code != 200:
                raise upstream_error(json_body(response), response.status_code, response.headers.get("x-request-id"))
            events = response_events(response)
            if not body.get("stream"):
                self.send_json(200, chat_completion(events, body.get("model")))
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                for chunk in chat_chunks(events, body.get("model")):
                    self.send_event(chunk)
                self.wfile.write(b"data: [DONE]\n\n")
            except BridgeError as error:
                # Headers are already sent; the OpenAI client raises on an error event.
                self.send_event({"error": {"message": error.message, "code": error.code, "type": "bridge_error"}})
            except requests.RequestException:
                self.send_event({"error": {"message": "The connection to ChatGPT was interrupted.", "code": "stream_interrupted", "type": "bridge_error"}})
            except (KeyError, TypeError, AttributeError):
                self.send_event({"error": {"message": "ChatGPT sent a response that could not be read.", "code": "invalid_stream", "type": "bridge_error"}})

    def send_event(self, data):
        self.wfile.write(b"data: " + json.dumps(data).encode() + b"\n\n")
        self.wfile.flush()


def main():
    global connection
    connection = Connection(STATE_PATH)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
