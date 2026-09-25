from typing import Protocol


class Store(Protocol):
    def add(self, text: str) -> None: ...
    def search(self, query: str, limit: int = 5) -> list[str]: ...


class KeywordStore:
    def __init__(self):
        self._items: list[str] = []

    def add(self, text: str) -> None:
        self._items.append(text)

    def search(self, query: str, limit: int = 5) -> list[str]:
        terms = {t for t in query.lower().split() if t}
        scored = [
            (sum(1 for t in terms if t in item.lower()), item) for item in self._items
        ]
        matches = sorted((s, i) for s, i in scored if s > 0)
        matches.reverse()
        return [item for _, item in matches[:limit]]
