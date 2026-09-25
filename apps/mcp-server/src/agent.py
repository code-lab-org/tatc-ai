from typing import Optional

from .conversation import assemble_context
from .models import LLMClient, default_client
from .rendering import render
from .retrieval import Store
from .tokens import TokenBudget, usage

SYSTEM_PROMPT = (
    "You are the TAT-C mission analysis assistant. "
    "Answer the task directly and state any assumptions you make. "
    "When the answer is tabular, return only a JSON array of flat objects."
)

_BUDGET = TokenBudget.from_env()


def run_task(
    task: str,
    client: Optional[LLMClient] = None,
    store: Optional[Store] = None,
) -> str:
    client = client or default_client()
    examples = store.search(task) if store else []
    prompt = assemble_context(task, examples=examples)
    response = client.generate(prompt, system=SYSTEM_PROMPT)
    _BUDGET.charge(response)
    extra = {"budget_exceeded": _BUDGET.exceeded} if _BUDGET.limit else None
    usage.record("run_agent_task", response, extra=extra)
    return render(response.text)
