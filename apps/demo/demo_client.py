from src.models.base import LLMTurn, ToolCall

_TLE = [
    "1 43013U 17073A   26022.94240312  .00000200  00000+0  11574-3 0  9998",
    "2 43013  98.7645 323.7982 0001179  28.0279 332.0961 14.19539020423863",
]
_SWATH = 3000e3
_TEMPE = (33.4255, -111.9400)
_DELTA_3x3 = {"configuration": "delta", "number_satellites": 3, "number_planes": 3}


def _plan(question: str):
    q = question.lower()
    has_const = any(w in q for w in ["constellation", "walker", "planes", "satellites in"])
    if "ground track" in q or "groundtrack" in q:
        return ToolCall("ground_track", {"tle": _TLE, "swath_width_m": _SWATH, "window_minutes": 30})
    if "coverage" in q or "global" in q:
        return ToolCall("global_coverage", {"tle": _TLE, "swath_width_m": _SWATH, "spacing_km": 5000,
                                            "constellation": _DELTA_3x3})
    if "revisit" in q:
        args = {"latitude": _TEMPE[0], "longitude": _TEMPE[1], "tle": _TLE, "swath_width_m": _SWATH}
        if has_const:
            args["constellation"] = _DELTA_3x3
        return ToolCall("revisit_over_point", args)
    return None


def _summarize(name, result):
    if name == "revisit_over_point":
        return f"Mean revisit period: {result:.2f} hours."
    if name == "ground_track":
        return f"Ground-track polygon computed ({len(result)} chars of WKT)."
    if name == "global_coverage":
        lo = min(r["mean_revisit_hr"] for r in result)
        hi = max(r["mean_revisit_hr"] for r in result)
        return f"Global coverage over {len(result)} points: revisit {lo:.2f}-{hi:.2f} hours."
    return str(result)


class DemoClient:
    name = "demo-offline"

    def converse(self, messages, tools, system=None):
        allowed = {t["name"] for t in tools}
        last = messages[-1]
        if last["role"] == "tool":
            text = _summarize(last["name"], last["result"])
            return LLMTurn(model=self.name, input_tokens=0, output_tokens=0, latency_s=0.0, text=text)
        question = next((m["text"] for m in messages if m["role"] == "user"), "")
        call = _plan(question)
        if call is None or call.name not in allowed:
            return LLMTurn(model=self.name, input_tokens=0, output_tokens=0, latency_s=0.0,
                           text="I can answer revisit, ground-track, or global-coverage questions.")
        return LLMTurn(model=self.name, input_tokens=0, output_tokens=0, latency_s=0.0, tool_calls=[call])
