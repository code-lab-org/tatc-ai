from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ConversationHistory:
    turns: list[str] = field(default_factory=list)

    def add(self, text: str) -> None:
        self.turns.append(text)

    def recent(self, limit: Optional[int] = None) -> list[str]:
        return list(self.turns) if limit is None else self.turns[-limit:]
