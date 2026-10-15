from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Role:
    name: str
    model: Optional[str] = None
    tools: tuple[str, ...] = ()
    capabilities: frozenset[str] = frozenset()
    token_budget: int = 0
    system_prompt: Optional[str] = None

    def can(self, capability: str) -> bool:
        return capability in self.capabilities
