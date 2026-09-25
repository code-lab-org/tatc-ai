from pathlib import Path
from typing import Protocol

from cases import Case

_SOLDIR = Path(__file__).parent / "solutions"


class AgentUnderTest(Protocol):
    name: str

    def solve(self, case: Case) -> str:
        ...


class ReferenceAgent:
    name = "reference"

    def solve(self, case: Case) -> str:
        return (_SOLDIR / case.solution).read_text()


class BaselineAgent:
    name = "baseline"

    def __init__(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
        from agent import run_task
        self._run = run_task

    def solve(self, case: Case) -> str:
        text = self._run(case.question)
        if "```python" in text:
            return text.split("```python", 1)[1].split("```", 1)[0]
        if "```" in text:
            return text.split("```", 1)[1].split("```", 1)[0]
        return text
