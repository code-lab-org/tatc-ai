from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_s: float


class LLMClient(Protocol):
    def generate(self, prompt: str, system: Optional[str] = None) -> LLMResponse: ...
