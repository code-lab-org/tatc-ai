"""Tool-calling eval for the TATC MCP server across ASU Voyager models.

Each model gets the server's real tool list and instructions, the way
LibreChat presents them (tools named <tool>_mcp_tatc, server instructions
in the system message), and works through the coverage cases in cases.py.
Tool calls run in-process against the real server through a FastMCP
client; CelesTrak and OSCAR responses replay from a recorded cache.
Checks score what the server actually computed.

Usage, from apps/mcp-server:
    .venv/bin/python -m eval.run_eval --label baseline
    .venv/bin/python -m eval.run_eval --label merged --tiers small
    .venv/bin/python -m eval.run_eval --compare eval/results/a.jsonl eval/results/b.jsonl

Reads OPENAI_API_KEY and OPENAI_REVERSE_PROXY from apps/librechat/.env.
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
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from fastmcp import Client

from .cases import Call, Clock, Trace, build_cases
from .http_replay import HttpReplay

HERE = Path(__file__).resolve().parent
ENV_FILE = Path(os.environ.get("EVAL_ENV_FILE", HERE.parents[1] / "librechat" / ".env"))
RESULTS = HERE / "results"
SUFFIX = "_mcp_tatc"
MAX_TURNS = 14
REQUEST_TIMEOUT = 240

# Picked from the proxy's chat models to span sizes. Thinking variants and
# models that took minutes to answer a one-tool probe are left out.
TIERS = {
    "small": [
        "olmo3-7b-instruct",
        "qwen3-30b-a3b-instruct-2507",
        "glm-5-3-flash",
        "granite41-30b",
    ],
    "medium": [
        "llama4-scout-17b",
        "gemma4-31b-it",
        "qwen36-27b",
        "olmo3-32b-instruct",
    ],
    "large": [
        "gpt-oss-120b",
        "qwen3-235b-a22b-instruct-2507",
        "glm-5-3",
        "kimi-k2-7-code",
    ],
}
TIER_OF = {model: tier for tier, models in TIERS.items() for model in models}


def load_env(path: Path) -> Dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def pin_host(host: str, ip: str) -> None:
    """Connect to host at ip, for machines whose hosts file pins a private IP."""
    original = socket.getaddrinfo

    def getaddrinfo(name, *args, **kwargs):
        return original(ip if name == host else name, *args, **kwargs)

    socket.getaddrinfo = getaddrinfo


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
        body = json.dumps(
            {"model": model, "messages": messages, "tools": self.tools, "max_tokens": 4096}
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


def run(args: argparse.Namespace) -> Path:
    env = load_env(ENV_FILE)
    if args.connect_ip:
        pin_host(urllib.parse.urlparse(env["OPENAI_REVERSE_PROXY"]).hostname, args.connect_ip)
    replay = HttpReplay(offline=args.offline)
    replay.install()
    clock = Clock(replay.recorded_at)
    harness = Harness(env["OPENAI_REVERSE_PROXY"], env["OPENAI_API_KEY"], args.hide, clock)
    cases = [c for c in build_cases(clock) if not args.cases or c.id in args.cases]
    models = args.models or [m for tier in args.tiers for m in TIERS[tier]]
    jobs = [(m, c, r) for m in models for c in cases for r in range(args.repeats)]

    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS / f"{stamp}-{args.label}.jsonl"
    lock = threading.Lock()
    print(f"{len(jobs)} conversations, tools: {', '.join(harness.tool_names)}", flush=True)

    def job(item):
        model, case, repeat = item
        started = time.time()
        record = {"label": args.label, "model": model, "tier": TIER_OF.get(model, "other"),
                  "case": case.id, "tags": case.tags, "repeat": repeat}
        try:
            trace = harness.converse(model, case.prompt)
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
    return out


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
    models = sorted({r["model"] for r in rows}, key=lambda m: (list(TIERS).index(TIER_OF[m]) if m in TIER_OF else 9, m))
    by = defaultdict(list)
    for r in rows:
        by[(r["label"], r["model"], r["case"])].append(r)
        by[(r["label"], r["model"], "*")].append(r)
        by[(r["label"], r["tier"], "*")].append(r)
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
        print("by tier: " + ", ".join(f"{t} {_rate(by[(label, t, '*')]).strip()}" for t in TIERS))
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
    parser.add_argument("--tiers", nargs="+", default=list(TIERS), choices=list(TIERS))
    parser.add_argument("--models", nargs="+", help="model IDs instead of tiers")
    parser.add_argument("--cases", nargs="+", help="case IDs to run, default all")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--hide", nargs="+", default=[], help="server tools to leave out")
    parser.add_argument("--offline", action="store_true", help="fail on HTTP responses not in the cache")
    parser.add_argument("--connect-ip", help="IP to reach the proxy at, when the hosts file pins a private one")
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
