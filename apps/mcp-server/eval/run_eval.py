"""Tool-calling eval for the TATC MCP server across ASU Voyager models.

Each model gets the server's real tool list and instructions, the way
LibreChat presents them (tools named <tool>_mcp_tatc, server instructions
in the system message), and works through the legacy coverage, conical, and
pointed study cases in cases.py.
Tool calls run in-process against the real server through a FastMCP
client; CelesTrak and OSCAR responses replay from a recorded cache.
Checks score what the server actually computed.

Usage, from apps/mcp-server:
    .venv/bin/python -m eval.run_eval --label baseline
    .venv/bin/python -m eval.run_eval --label trial --models glm-5-3 qwen38-27b
    .venv/bin/python -m eval.run_eval --compare eval/results/a.jsonl eval/results/b.jsonl
    .venv/bin/python -m eval.run_eval --stream-only    # just the streaming probe

Reads VOYAGER_API_KEY and VOYAGER_BASE_URL from apps/librechat/.env.
Sends prompts and tool schemas to the ASU proxy; no project code or data.
"""

import argparse
import asyncio
import json
import os
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
REQUEST_TIMEOUT = 240

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


class Harness:
    def __init__(self, base_url: str, api_key: str, hide: Sequence[str], clock: Clock):
        import src.generic_coverage as gc
        import src.server as server

        # Pin "now" for OMM freshness so a replayed cache keeps validating.
        original = gc._validate_omm_freshness
        gc._validate_omm_freshness = lambda records, norad, now=None: original(records, norad, clock.now)
        self.server = server
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.clock = clock
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

    def call_tool(self, name: str, args: Dict[str, Any]):
        async def run():
            async with Client(self.server.mcp) as client:
                return await client.call_tool(name, args, raise_on_error=False)

        result = asyncio.run(run())
        text = "\n".join(getattr(part, "text", "") for part in result.content)
        return result.is_error, text

    def chat(self, model: str, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        # A complete sourced configuration must fit in one tool-call response.
        body = json.dumps(
            {"model": model, "messages": messages, "tools": self.tools, "max_tokens": 8192}
        ).encode()
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=body,
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                    return json.load(response)["choices"][0]["message"]
            except urllib.error.HTTPError as exc:
                detail = exc.read()[:300]
                if b"ContextWindowExceeded" in detail:
                    raise ContextOverflow() from exc
                if exc.code < 500 and exc.code != 429:
                    raise RuntimeError(f"HTTP {exc.code}: {detail!r}") from exc
                error: Exception = exc
            except (TimeoutError, urllib.error.URLError, KeyError, IndexError) as exc:
                error = exc
            time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"model call failed: {error!r}")

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
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                lines = [line.decode().strip() for line in response]
        except OSError as exc:  # includes URLError and HTTPError
            return f"error {exc!r}"[:80]
        return judge_stream([json.loads(line[5:]) for line in lines
                             if line.startswith("data:") and "[DONE]" not in line])

    def converse(self, model: str, prompt: str) -> Trace:
        trace = Trace(prompt=prompt)
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": prompt},
        ]
        for _ in range(MAX_TURNS):
            trace.turns += 1
            try:
                message = self.chat(model, messages)
            except ContextOverflow:
                trace.stop = "context_overflow"
                return trace
            calls = message.get("tool_calls") or []
            if not calls:
                trace.final = message.get("content") or ""
                trace.stop = "answered"
                return trace
            messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
            for tool_call in calls:
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
                        error, text = self.call_tool(name, args)
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


def _converse_with_case_state(harness: Harness, model: str, case) -> Trace:
    """Keep mutable registry refreshes local to each concurrent eval conversation."""
    with isolated_refresh_state():
        return harness.converse(model, case.prompt)


def run(args: argparse.Namespace) -> None:
    env = load_env(ENV_FILE)
    replay = HttpReplay(offline=args.offline)
    replay.install()
    clock = Clock(replay.recorded_at)
    harness = Harness(env["VOYAGER_BASE_URL"], env["VOYAGER_API_KEY"], args.hide, clock)
    cases = [c for c in build_cases(clock) if not args.cases or c.id in args.cases]
    models = args.models or MODELS
    streams = probe_streams(harness, models)
    if args.stream_only:
        print("\n".join(streams))
        return
    jobs = [(m, c, r) for m in models for c in cases for r in range(args.repeats)]

    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS / f"{stamp}-{args.label}.jsonl"
    lock = threading.Lock()
    print(f"{len(jobs)} conversations, tools: {', '.join(harness.tool_names)}", flush=True)

    def job(item):
        model, case, repeat = item
        started = time.time()
        record = {"label": args.label, "model": model,
                  "case": case.id, "tags": case.tags, "repeat": repeat}
        try:
            trace = _converse_with_case_state(harness, model, case)
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
                trace=asdict(trace),
            )
        except Exception as exc:  # infra failures are reported, not scored
            record.update(status="infra_error", error=repr(exc)[:300])
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
            if row["status"] != "ok" or row["case"] not in cases:
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
    parser.add_argument("--models", nargs="+", help="model IDs, default the LibreChat set")
    parser.add_argument("--cases", nargs="+", help="case IDs to run, default all")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--hide", nargs="+", default=[], help="server tools to leave out")
    parser.add_argument("--stream-only", action="store_true", help="only run the streaming probe")
    parser.add_argument("--offline", action="store_true", help="fail on HTTP responses not in the cache")
    parser.add_argument("--compare", nargs="+", type=Path, help="summarize saved result files and exit")
    parser.add_argument("--rescore", action="store_true", help="with --compare, recheck traces first")
    args = parser.parse_args(argv)
    if args.compare:
        if args.rescore:
            rescore(args.compare)
        summarize(args.compare)
        return 0
    run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
