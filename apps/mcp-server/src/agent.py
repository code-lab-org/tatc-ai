from typing import Optional

from .conversation import assemble_context
from .models import LLMClient, client_for
from .rendering import render
from .retrieval import Store
from .roles import Role, get as get_role
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
    role: Optional[Role] = None,
) -> str:
    role = role or get_role()
    client = client or client_for(role.model)
    budget = TokenBudget(role.token_budget) if role.token_budget else _BUDGET
    system = role.system_prompt or SYSTEM_PROMPT
    examples = store.search(task) if store else []
    prompt = assemble_context(task, examples=examples)
    response = client.generate(prompt, system=system)
    budget.charge(response)
    extra = {"role": role.name}
    if budget.limit:
        extra["budget_exceeded"] = budget.exceeded
    usage.record("run_agent_task", response, extra=extra)
    return render(response.text)
