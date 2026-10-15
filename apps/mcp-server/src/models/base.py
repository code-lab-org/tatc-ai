from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_s: float


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class LLMTurn:
    model: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    text: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient(Protocol):
    def generate(self, prompt: str, system: Optional[str] = None) -> LLMResponse: ...


class ToolClient(Protocol):
    def converse(
        self, messages: list[dict], tools: list[dict], system: Optional[str] = None
    ) -> LLMTurn: ...
