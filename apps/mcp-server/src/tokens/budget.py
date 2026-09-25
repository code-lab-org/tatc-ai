import os

from ..models.base import LLMResponse


class TokenBudget:
    def __init__(self, limit: int = 0):
        self.limit = limit
        self.used = 0

    @classmethod
    def from_env(cls) -> "TokenBudget":
        return cls(int(os.environ.get("TASK_TOKEN_BUDGET", "0")))

    def charge(self, response: LLMResponse) -> None:
        self.used += response.input_tokens + response.output_tokens

    @property
    def exceeded(self) -> bool:
        return bool(self.limit) and self.used > self.limit
