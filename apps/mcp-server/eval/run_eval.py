"""Tool-calling eval for the TATC MCP server across ASU Voyager models.

Each model gets the server's real tool list and instructions, the way
LibreChat presents them (tools named <tool>_mcp_tatc, server instructions
in the system message), and works through the legacy coverage, conical, and
pointed study cases in cases.py.
Tool calls run in-process against the real server through a FastMCP
client; CelesTrak and OSCAR responses replay from a recorded cache.
Checks score what the server actually computed.

Usage, from apps/mcp-server:
    .venv312/bin/python -m eval.run_eval --label baseline
    .venv312/bin/python -m eval.run_eval --label trial --models glm-5-3 qwen38-27b
    .venv312/bin/python -m eval.run_eval --label affected --repeats 3
    .venv312/bin/python -m eval.run_eval --compare eval/results/a.jsonl eval/results/b.jsonl
    .venv312/bin/python -m eval.run_eval --stream-only    # just the streaming probe

The routine model is deepseek-v4-1-flash. Select others explicitly with
--models, or every Voyager model with --all-models. Budgets default to 180
seconds per request and 600 seconds per conversation; override them with
--request-timeout and --conversation-timeout. The result file keeps only
scores unless --save-traces is passed. A small per-request file records
model, case, turn, attempt, bytes, duration, usage, request ID, and error
category without prompts or tool text.

Reads VOYAGER_API_KEY and VOYAGER_BASE_URL from apps/librechat/.env.
Sends prompts and tool schemas to the ASU proxy; no project code or data.
"""

import argparse
import asyncio
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml
from fastmcp import Client

from src.configuration_registry import isolated_refresh_state

from .cases import Call, Clock, Trace, build_cases
from .http_replay import HttpReplay

HERE = Path(__file__).resolve().parent
ENV_FILE = Path(os.environ.get("EVAL_ENV_FILE", HERE.parents[1] / "librechat" / ".env"))
RESULTS = HERE / "results"
SUFFIX = "_mcp_tatc"
MAX_TURNS = 14
REQUEST_TIMEOUT = 180
CONVERSATION_TIMEOUT = 600
MAX_REQUEST_ATTEMPTS = 2
RETRYABLE_STATUS = {429, 502, 503}
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_ERROR_BYTES = 1024 * 1024
CONTEXT_MARKERS = (
    "contextwindowexceeded",
    "context length",
    "context window",
    "maximum context",
    "too many tokens",
)

# The ASU Voyager models TATC AI offers, read from LibreChat's modelSpecs so
# the two lists cannot drift apart; pass --models to try others. CreateAI
# specs are skipped: the eval talks to the Voyager proxy.
LIBRECHAT_CONFIG = HERE.parents[1] / "librechat" / "librechat.yaml"
_PRESETS = [
    spec["preset"]
    for spec in yaml.safe_load(LIBRECHAT_CONFIG.read_text())["modelSpecs"]["list"]
    if spec["preset"]["endpoint"] == "ASU Voyager"
]
MODELS = [preset["model"] for preset in _PRESETS]
DEFAULT_MODELS = ["deepseek-v4-1-flash"]
# Models LibreChat is told not to stream; the probe reports them without failing.
NO_STREAMING = {preset["model"] for preset in _PRESETS if preset.get("disableStreaming")}
STREAM_PROMPT = "Look up the ISS with the satellite search tool."


def judge_stream(events: List[Dict[str, Any]]) -> str:
    """Judge streamed chunks the way LibreChat's LangChain client reads them.

    It starts a message from the first delta's role and drops tool calls
    streamed without one.
    """
    deltas = [e["choices"][0]["delta"] for e in events if e.get("choices")]
    if not deltas or deltas[0].get("role") != "assistant":
        return "missing role"
    if not any(d.get("tool_calls") for d in deltas):
        return "no tool call"
    return "ok"


def load_env(path: Path) -> Dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


class ContextOverflow(Exception):
    """The conversation outgrew the model's context; scored as a failure."""


class RequestTimeout(Exception):
    """One request exceeded its wall-clock budget."""


class ConversationTimeout(Exception):
    """One conversation or tool call exceeded its wall-clock budget."""


class ModelCallError(Exception):
    """A model request failed; ``category`` is safe to log."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


def _remaining(deadline: float) -> float:
    return deadline - time.monotonic()


def _set_response_timeout(response: Any, seconds: float) -> None:
    """Keep a trickle-reading response inside a total deadline."""
    try:
        response.fp.raw._sock.settimeout(max(0.001, seconds))
    except (AttributeError, OSError):
        pass


def _read_bounded(
    response: Any, deadline: float, *, max_bytes: int = MAX_RESPONSE_BYTES
) -> bytes:
    """Read a response in chunks, enforcing a total wall-clock deadline."""
    chunks = []
    total = 0
    read = getattr(response, "read1", None)
    if read is None:
        read = getattr(getattr(response, "fp", None), "read1", None)
    if read is None:
        read = response.read
    while True:
        remaining = _remaining(deadline)
        if remaining <= 0:
            raise RequestTimeout("request budget expired while reading the response")
        _set_response_timeout(response, remaining)
        try:
            chunk = read(65536)
        except (socket.timeout, TimeoutError) as exc:
            raise RequestTimeout(
                "request budget expired while reading the response"
            ) from exc
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ModelCallError(
                "response_too_large", f"response exceeded {max_bytes} bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _has_context_marker(body: bytes) -> bool:
    text = body.decode("utf-8", "replace").lower()
    return any(marker in text for marker in CONTEXT_MARKERS)


def _error_category(exc: BaseException) -> str:
    if isinstance(exc, (RequestTimeout, ConversationTimeout, TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, ContextOverflow):
        return "context_overflow"
    if isinstance(exc, ModelCallError):
        return exc.category
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return "connection"
    return "harness_error"


def request_completion(
    base_url: str,
    api_key: str,
    body: bytes,
    *,
    request_timeout: float,
    conversation_deadline: float,
    record: Any = None,
) -> Dict[str, Any]:
    """Post one completion request with bounded reads and one retry.

    Retries only a connection failure, 429, 502, or 503. Read timeouts,
    context overflow, and other client errors fail immediately. The retry
    shares the conversation deadline, so no attempt can outlive it.
    """
    error: Optional[BaseException] = None
    category = ""
    for attempt in range(MAX_REQUEST_ATTEMPTS):
        remaining = _remaining(conversation_deadline)
        if remaining <= 0:
            raise RequestTimeout("conversation budget expired before the request")
        request_deadline = time.monotonic() + min(request_timeout, remaining)
        started = time.monotonic()
        response_bytes = 0
        usage = None
        request_id = None
        try:
            request = urllib.request.Request(
                base_url + "/chat/completions",
                data=body,
                headers={
                    "Authorization": "Bearer " + api_key,
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(
                request, timeout=max(0.001, _remaining(request_deadline))
            ) as response:
                raw = _read_bounded(response, request_deadline)
                response_bytes = len(raw)
                payload = json.loads(raw)
                usage = payload.get("usage")
                request_id = payload.get("id")
                message = payload["choices"][0]["message"]
                if record is not None:
                    record(
                        attempt=attempt,
                        request_bytes=len(body),
                        response_bytes=response_bytes,
                        duration=time.monotonic() - started,
                        usage=usage,
                        request_id=request_id,
                        error_category=None,
                    )
                return message
        except RequestTimeout as exc:
            error, category = exc, "timeout"
        except ModelCallError as exc:
            error, category = exc, exc.category
        except urllib.error.HTTPError as exc:
            # Read the whole bounded error body: proxy context errors can sit
            # well past the first few hundred bytes.
            try:
                raw = _read_bounded(exc, request_deadline, max_bytes=MAX_ERROR_BYTES)
                response_bytes = len(raw)
            except RequestTimeout as timeout_exc:
                error, category = timeout_exc, "timeout"
            else:
                if _has_context_marker(raw):
                    error, category = ContextOverflow(), "context_overflow"
                else:
                    category = f"http_{exc.code}"
                    detail = raw[:300].decode("utf-8", "replace")
                    error = ModelCallError(category, f"HTTP {exc.code}: {detail!r}")
        except (TimeoutError, socket.timeout) as exc:
            error, category = exc, "timeout"
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(
                reason
            ).lower():
                error, category = exc, "timeout"
            else:
                error, category = exc, "connection"
        except (ConnectionError, OSError) as exc:
            error, category = exc, "connection"
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            error, category = exc, "protocol"
        if record is not None:
            record(
                attempt=attempt,
                request_bytes=len(body),
                response_bytes=response_bytes,
                duration=time.monotonic() - started,
                usage=usage,
                request_id=request_id,
                error_category=category,
            )
        if isinstance(error, ContextOverflow):
            raise error
        if category not in ("connection", "http_429", "http_502", "http_503"):
            break
    if isinstance(error, RequestTimeout):
        raise error
    if category == "timeout":
        raise RequestTimeout(f"request budget expired: {error!r}")
    if isinstance(error, ModelCallError):
        raise error
    if isinstance(error, ContextOverflow):
        raise error
    raise ModelCallError(category or "harness_error", f"model call failed: {error!r}")


class Harness:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        hide: Sequence[str],
        clock: Clock,
        *,
        request_timeout: float = REQUEST_TIMEOUT,
        conversation_timeout: float = CONVERSATION_TIMEOUT,
    ):
        import src.generic_coverage as gc
        import src.server as server

        # Pin "now" for OMM freshness so a replayed cache keeps validating.
        original = gc._validate_omm_freshness
        gc._validate_omm_freshness = lambda records, norad, now=None: original(records, norad, clock.now)
        self.server = server
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.clock = clock
        self.request_timeout = request_timeout
        self.conversation_timeout = conversation_timeout
        tools = asyncio.run(self._list_tools())
        self.tool_names = [t.name for t in tools if t.name not in hide]
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name + SUFFIX,
                    "description": t.description or "",
                    "parameters": t.inputSchema,
                },
            }
            for t in tools
            if t.name not in hide
        ]
        # LibreChat appends server instructions to the system message; it
        # adds no date by default, so the eval states "now" explicitly.
        self.system = (
            f"Current date and time: {clock.now.strftime('%Y-%m-%d %H:%M')} UTC.\n\n"
            f"## MCP Server Instructions\n### tatc\n{server.SERVER_INSTRUCTIONS}"
        )

    async def _list_tools(self):
        async with Client(self.server.mcp) as client:
            return await client.list_tools()

    def call_tool(
        self, name: str, args: Dict[str, Any], *, deadline: Optional[float] = None
    ):
        """Run one MCP tool call inside the conversation's hard deadline."""
        deadline = deadline or time.monotonic() + self.conversation_timeout
        result: Dict[str, Any] = {}

        def run():
            try:
                async def call():
                    async with Client(self.server.mcp) as client:
                        return await client.call_tool(name, args, raise_on_error=False)

                result["value"] = asyncio.run(call())
            except BaseException as exc:  # handed back to the conversation thread
                result["error"] = exc

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(max(0.0, _remaining(deadline)))
        if thread.is_alive():
            raise ConversationTimeout(
                f"tool {name} exceeded the conversation budget"
            )
        if "error" in result:
            raise result["error"]
        value = result["value"]
        text = "\n".join(getattr(part, "text", "") for part in value.content)
        return value.is_error, text

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        *,
        deadline: Optional[float] = None,
        case_id: Optional[str] = None,
        turn: Optional[int] = None,
        recorder: Any = None,
    ) -> Dict[str, Any]:
        # A complete sourced configuration must fit in one tool-call response.
        body = json.dumps(
            {"model": model, "messages": messages, "tools": self.tools, "max_tokens": 8192}
        ).encode()
        deadline = deadline or time.monotonic() + self.conversation_timeout
        record = None
        if recorder is not None:
            record = lambda **row: recorder(
                model=model, case=case_id, turn=turn, **row
            )
        return request_completion(
            self.base_url,
            self.api_key,
            body,
            request_timeout=self.request_timeout,
            conversation_deadline=deadline,
            record=record,
        )

    def probe_stream(self, model: str) -> str:
        """Stream one short tool-using request and judge it with judge_stream."""
        body = json.dumps({
            "model": model, "stream": True, "max_tokens": 512, "tools": self.tools,
            "messages": [{"role": "system", "content": self.system},
                         {"role": "user", "content": STREAM_PROMPT}],
        }).encode()
        request = urllib.request.Request(
            self.base_url + "/chat/completions", data=body,
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(
                request, timeout=self.request_timeout
            ) as response:
                raw = _read_bounded(
                    response, time.monotonic() + self.request_timeout
                )
        except (OSError, RequestTimeout) as exc:  # includes URLError and HTTPError
            return f"error {exc!r}"[:80]
        lines = [line.decode().strip() for line in raw.splitlines()]
        return judge_stream([json.loads(line[5:]) for line in lines
                             if line.startswith("data:") and "[DONE]" not in line])

    def converse(
        self,
        model: str,
        prompt: str,
        *,
        case_id: Optional[str] = None,
        recorder: Any = None,
        deadline: Optional[float] = None,
    ) -> Trace:
        trace = Trace(prompt=prompt)
        deadline = deadline or time.monotonic() + self.conversation_timeout
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": prompt},
        ]
        for _ in range(MAX_TURNS):
            if _remaining(deadline) <= 0:
                trace.stop = "timeout"
                trace.error_category = "timeout"
                trace.error = "conversation budget expired before the next turn"
                return trace
            trace.turns += 1
            try:
                message = self.chat(
                    model,
                    messages,
                    deadline=deadline,
                    case_id=case_id,
                    turn=trace.turns,
                    recorder=recorder,
                )
            except ContextOverflow:
                trace.stop = "context_overflow"
                return trace
            except (RequestTimeout, ConversationTimeout) as exc:
                trace.stop = "timeout"
                trace.error_category = "timeout"
                trace.error = str(exc)[:300]
                return trace
            except ModelCallError as exc:
                trace.stop = "infra_error"
                trace.error_category = exc.category
                trace.error = str(exc)[:300]
                return trace
            calls = message.get("tool_calls") or []
            if not calls:
                trace.final = message.get("content") or ""
                trace.stop = "answered"
                return trace
            messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
            for tool_call in calls:
                if _remaining(deadline) <= 0:
                    trace.stop = "timeout"
                    trace.error_category = "timeout"
                    trace.error = "conversation budget expired before a tool call"
                    return trace
                call_id = tool_call.get("id") or uuid.uuid4().hex[:12]
                tool_call["id"] = call_id
                raw_name = tool_call["function"]["name"]
                name = raw_name[: -len(SUFFIX)] if raw_name.endswith(SUFFIX) else raw_name
                try:
                    args = json.loads(tool_call["function"].get("arguments") or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be a JSON object")
                except ValueError as exc:
                    args, error, text = {}, True, f"Error: tool arguments were not valid JSON: {exc}"
                else:
                    if name not in self.tool_names:
                        error, text = True, f"Error: tool not found: {raw_name}"
                    else:
                        try:
                            error, text = self.call_tool(name, args, deadline=deadline)
                        except ConversationTimeout as exc:
                            trace.stop = "timeout"
                            trace.error_category = "timeout"
                            trace.error = str(exc)[:300]
                            trace.calls.append(
                                Call(name, args, True, f"Error: {exc}", None)
                            )
                            return trace
                parsed = None
                if not error:
                    try:
                        parsed = json.loads(text)
                    except ValueError:
                        pass
                trace.calls.append(Call(name, args, error, text, parsed if isinstance(parsed, dict) else None))
                messages.append({"role": "tool", "tool_call_id": call_id, "content": text})
        trace.stop = "turn_limit"
        return trace


def probe_streams(harness: Harness, models: Sequence[str]) -> List[str]:
    """Report one line per model; models in NO_STREAMING are noted, not failed."""
    with ThreadPoolExecutor(len(models)) as pool:
        verdicts = list(pool.map(harness.probe_stream, models))
    return [
        f"stream: {model:32} {verdict}"
        + (" (disableStreaming)" if model in NO_STREAMING else "" if verdict == "ok" else "  <- FAIL")
        for model, verdict in zip(models, verdicts)
    ]


def _converse_with_case_state(
    harness: Harness, model: str, case, recorder: Any = None
) -> Trace:
    """Keep mutable registry refreshes local to each concurrent eval conversation."""
    with isolated_refresh_state():
        return harness.converse(
            model,
            case.prompt,
            case_id=getattr(case, "id", None),
            recorder=recorder,
        )


def _selected_models(args: argparse.Namespace) -> List[str]:
    """Resolve a single primary model by default and fail on catalog drift."""
    if args.all_models:
        models = list(MODELS)
    elif args.models:
        models = list(args.models)
    else:
        models = list(DEFAULT_MODELS)
    if not models:
        raise ValueError(
            "No Voyager models are configured in apps/librechat/librechat.yaml"
        )
    unknown = [model for model in models if model not in MODELS]
    if unknown:
        raise ValueError(
            f"Model(s) {unknown!r} are not in the LibreChat ASU Voyager catalog "
            f"{MODELS!r}. Update apps/librechat/librechat.yaml or pass an exact "
            "catalog model. No fallback model is selected."
        )
    return models


def run(args: argparse.Namespace) -> None:
    env = load_env(ENV_FILE)
    replay = HttpReplay(offline=args.offline)
    replay.install()
    clock = Clock(replay.recorded_at)
    models = _selected_models(args)
    harness = Harness(
        env["VOYAGER_BASE_URL"],
        env["VOYAGER_API_KEY"],
        args.hide,
        clock,
        request_timeout=args.request_timeout,
        conversation_timeout=args.conversation_timeout,
    )
    cases = [c for c in build_cases(clock) if not args.cases or c.id in args.cases]
    streams = probe_streams(harness, models)
    if args.stream_only:
        print("\n".join(streams))
        return
    # Round-robin models and cases so a slow model cannot monopolize workers.
    jobs = [
        (model, case, repeat)
        for case in cases
        for repeat in range(args.repeats)
        for model in models
    ]

    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS / f"{stamp}-{args.label}.jsonl"
    request_log = out.with_name(out.stem + "-requests.jsonl")
    lock = threading.Lock()
    print(f"{len(jobs)} conversations, tools: {', '.join(harness.tool_names)}", flush=True)
    print(f"request records: {request_log}", flush=True)

    def record_request(**row):
        entry = {
            "model": row["model"],
            "case": row["case"],
            "turn": row["turn"],
            "attempt": row["attempt"],
            "bytes": {
                "request": row["request_bytes"],
                "response": row["response_bytes"],
            },
            "duration_seconds": round(row["duration"], 3),
            "usage": row["usage"],
            "request_id": row["request_id"],
            "error_category": row["error_category"],
        }
        with lock:
            with request_log.open("a") as handle:
                handle.write(json.dumps(entry, default=str) + "\n")

    def job(item):
        model, case, repeat = item
        started = time.time()
        record = {"label": args.label, "model": model,
                  "case": case.id, "tags": case.tags, "repeat": repeat}
        try:
            trace = _converse_with_case_state(harness, model, case, record_request)
            if trace.error_category:
                record.update(
                    status="infra_error",
                    error_category=trace.error_category,
                    error=trace.error[:300],
                    stop=trace.stop,
                    turns=trace.turns,
                    tool_calls=len(trace.calls),
                    partial={
                        "stop": trace.stop,
                        "turns": trace.turns,
                        "tool_calls": [
                            {"name": call.name, "error": call.error}
                            for call in trace.calls
                        ],
                    },
                )
            else:
                checks = case.checks(trace, clock)
                summary_id = next(
                    (c.parsed["result_id"] for c in reversed(trace.calls) if c.parsed and c.parsed.get("result_id")),
                    None,
                )
                record.update(
                    status="ok",
                    passed=all(checks.values()),
                    checks=checks,
                    tool_errors=sum(c.error for c in trace.calls),
                    tool_calls=len(trace.calls),
                    turns=trace.turns,
                    stop=trace.stop,
                    reported_result_id=bool(summary_id and summary_id in trace.final),
                )
            if args.save_traces:
                record["trace"] = asdict(trace)
        except Exception as exc:  # infra failures are reported, not scored
            record.update(
                status="infra_error",
                error=repr(exc)[:300],
                error_category=_error_category(exc),
            )
        record["seconds"] = round(time.time() - started, 1)
        with lock:
            with out.open("a") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
            mark = "infra" if record["status"] != "ok" else ("PASS" if record["passed"] else "fail")
            print(f"{mark:5} {model:32} {case.id:15} r{repeat} {record['seconds']}s", flush=True)

    try:
        with ThreadPoolExecutor(args.workers) as pool:
            list(pool.map(job, jobs))
    finally:
        replay.save()
    summarize([out])
    print("\n" + "\n".join(streams))


def _load(paths: Sequence[Path]) -> List[Dict[str, Any]]:
    rows = []
    for path in paths:
        rows.extend(json.loads(line) for line in Path(path).read_text().splitlines() if line.strip())
    return rows


def _rate(rows: List[Dict[str, Any]], key: str = "passed") -> str:
    scored = [r for r in rows if r["status"] == "ok"]
    if not scored:
        return "  -  "
    return f"{100 * sum(bool(r[key]) for r in scored) / len(scored):3.0f}%"


def summarize(paths: Sequence[Path]) -> None:
    rows = _load(paths)
    labels = sorted({r["label"] for r in rows})
    cases = sorted({r["case"] for r in rows})
    models = sorted({r["model"] for r in rows}, key=lambda m: (MODELS.index(m) if m in MODELS else 99, m))
    by = defaultdict(list)
    for r in rows:
        by[(r["label"], r["model"], r["case"])].append(r)
        by[(r["label"], r["model"], "*")].append(r)
        by[(r["label"], "*", r["case"])].append(r)
        by[(r["label"], "*", "*")].append(r)
    for label in labels:
        print(f"\n== {label}: pass rate by model and case")
        print(f"{'model':34}" + "".join(f"{c[:12]:>13}" for c in cases) + f"{'all':>7}{'errs/run':>10}")
        for model in models:
            group = by[(label, model, "*")]
            scored = [r for r in group if r["status"] == "ok"]
            errors = sum(r["tool_errors"] for r in scored) / len(scored) if scored else 0
            infra = len(group) - len(scored)
            line = f"{model[:33]:34}" + "".join(f"{_rate(by[(label, model, c)]):>13}" for c in cases)
            print(line + f"{_rate(group):>7}{errors:>10.1f}" + (f"  ({infra} infra)" if infra else ""))
        print(f"{'ALL':34}" + "".join(f"{_rate(by[(label, '*', c)]):>13}" for c in cases) + f"{_rate(by[(label, '*', '*')]):>7}")
        print(f"result ID reported in reply: {_rate(by[(label, '*', '*')], 'reported_result_id').strip()}")
    if len(labels) > 1:
        print("\n== failed checks by label")
        for label in labels:
            counts = defaultdict(int)
            for r in by[(label, "*", "*")]:
                for name, ok in (r.get("checks") or {}).items():
                    counts[name] += not ok
            print(f"{label}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))


def rescore(paths: Sequence[Path]) -> None:
    """Recheck saved traces with the current cases, rewriting the files."""
    clock = Clock(HttpReplay(offline=True).recorded_at)
    cases = {c.id: c for c in build_cases(clock)}
    for path in paths:
        rows = _load([path])
        for row in rows:
            if row["status"] != "ok" or row["case"] not in cases or "trace" not in row:
                continue
            raw = row["trace"]
            trace = Trace(
                prompt=raw["prompt"],
                calls=[Call(**call) for call in raw["calls"]],
                final=raw["final"],
                turns=raw["turns"],
                stop=raw["stop"],
            )
            checks = cases[row["case"]].checks(trace, clock)
            row.update(checks=checks, passed=all(checks.values()))
        Path(path).write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default="run", help="name for this tool set in the results")
    model_selection = parser.add_mutually_exclusive_group()
    model_selection.add_argument("--models", nargs="+", help="model IDs, default deepseek-v4-1-flash")
    model_selection.add_argument("--all-models", action="store_true", help="run every Voyager model in LibreChat")
    parser.add_argument("--cases", nargs="+", help="case IDs to run, default all")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=REQUEST_TIMEOUT,
        help="wall-clock seconds for one model request (default 180)",
    )
    parser.add_argument(
        "--conversation-timeout",
        type=float,
        default=CONVERSATION_TIMEOUT,
        help="wall-clock seconds for one complete conversation (default 600)",
    )
    parser.add_argument(
        "--save-traces",
        action="store_true",
        help="keep full prompts, replies, and tool text in the result file",
    )
    parser.add_argument("--hide", nargs="+", default=[], help="server tools to leave out")
    parser.add_argument("--stream-only", action="store_true", help="only run the streaming probe")
    parser.add_argument("--offline", action="store_true", help="fail on HTTP responses not in the cache")
    parser.add_argument("--compare", nargs="+", type=Path, help="summarize saved result files and exit")
    parser.add_argument("--rescore", action="store_true", help="with --compare, recheck traces first")
    args = parser.parse_args(argv)
    if args.request_timeout <= 0 or args.conversation_timeout <= 0:
        parser.error("request and conversation timeouts must be positive")
    if args.compare:
        if args.rescore:
            rescore(args.compare)
        summarize(args.compare)
        return 0
    run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
