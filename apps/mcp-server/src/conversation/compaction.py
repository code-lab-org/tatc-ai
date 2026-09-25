from typing import Protocol


class Compactor(Protocol):
    def compact(self, turns: list[str]) -> list[str]: ...


class NullCompactor:
    def compact(self, turns: list[str]) -> list[str]:
        return list(turns)
