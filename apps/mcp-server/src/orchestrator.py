from dataclasses import dataclass, field
from typing import Optional, Protocol

from .models import LLMResponse, ToolClient
from .roles import Role, get as get_role
from .tokens import usage

SYSTEM_PROMPT = (
    "You are the TAT-C mission analysis assistant. "
    "Use the provided tools to answer the request; call a tool for every "
    "quantitative result and never compute values yourself. "
    "When you have all results, give a concise final answer."
)


class Toolset(Protocol):
    def schemas(self) -> list[dict]: ...
    def call(self, name: str, **kwargs): ...


@dataclass
class Step:
    tool: str
    arguments: dict
    result: object


@dataclass
class AgentRun:
    answer: str
    steps: list = field(default_factory=list)
    turns: int = 0
    truncated: bool = False


def run(
    task: str,
    client: ToolClient,
    toolset: Optional[Toolset] = None,
    role: Optional[Role] = None,
    max_steps: int = 6,
) -> AgentRun:
    if toolset is None:
        from .tools import registry as toolset
    role = role or get_role()
    all_names = {s["name"] for s in toolset.schemas()}
    allowed = set(role.tools) & all_names if role.tools else all_names
    schemas = [s for s in toolset.schemas() if s["name"] in allowed]
    system = role.system_prompt or SYSTEM_PROMPT
    messages: list[dict] = [{"role": "user", "text": task}]
    steps: list[Step] = []
    for step_index in range(max_steps):
        turn = client.converse(messages, schemas, system=system)
        usage.record(
            "run_agent_loop",
            LLMResponse(
                text=turn.text or "",
                model=turn.model,
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
                latency_s=turn.latency_s,
            ),
            extra={"role": role.name, "step": step_index},
        )
        if not turn.tool_calls:
            return AgentRun(answer=turn.text or "", steps=steps, turns=step_index + 1)
        messages.append({"role": "assistant", "tool_calls": turn.tool_calls})
        for tool_call in turn.tool_calls:
            if tool_call.name not in allowed:
                messages.append(
                    {
                        "role": "tool",
                        "name": tool_call.name,
                        "result": {"error": "tool not permitted for role"},
                    }
                )
                continue
            result = toolset.call(tool_call.name, **tool_call.arguments)
            steps.append(Step(tool_call.name, tool_call.arguments, result))
            messages.append({"role": "tool", "name": tool_call.name, "result": result})
    return AgentRun(answer="", steps=steps, turns=max_steps, truncated=True)
