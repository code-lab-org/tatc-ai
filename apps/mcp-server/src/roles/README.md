# roles — per-role agent configuration

Parameterizes the single agent by **role**, so one agent design serves many
callers with different models/APIs, tool scopes, token budgets and system
prompts. This is deliberately *not* a multi-agent orchestration framework — see
"Roles are configuration, not agents."

## Interface

`Role` — `name`, `model`, `tools`, `token_budget`, `system_prompt`.
`registry.get(name)` returns a role (defaults to `user`); `ROLES` holds the
baseline set. `agent.run_task(task, role=...)` binds it: `model` →
`client_for`, `token_budget` → the run's budget, `system_prompt` → the system
prompt, `tools` → the caller/tool layer's allow-list (see the caveat).

Baseline: `user`, `developer`, `contributor`, `admin`, all fields at defaults —
identical to current behavior. The honest control: nothing differs between
roles until you define each one's policy.

## Roles are configuration, not agents

"An agent for admin, one for developer, some for users" is not multi-agent
collaboration — those personas do not work together on one task, they are
different callers. That is role-scoped configuration of ONE agent: choose a
model/API, a tool scope, a budget, a prompt. It needs a dataclass, not a
framework, and adds an Nth role for free.

True multi-agent orchestration — several agents decomposing and collaborating on
a SINGLE task (a planner + a `tatc` coder + a verifier on the snow-study case) —
is a separate, heavier concern with message passing and shared state. Reach for
a framework there, later, when a hard case earns the complexity. Building it just
to get per-role models would be over-engineering.

## Where the model-routing research lives

Roles are the natural home for the open-vs-public split: an admin / critical
role → a powerful public LLM; a display or formatting role → a cheap or local
open model; a privacy-sensitive role → on-prem only. Set `role.model` and extend
`router.client_for` to route by it.

## Caveat: enforce tool access, do not prompt it

`role.tools` is an authorization boundary. Enforce it in code — gate which tools
are registered or callable for a role — never by telling the model "you are a
user, do not use admin tools." A prompt is not a security control.

## First step

Define each role's model, tools and budget (your decisions), then A/B roles on
the eval harness: same cases, different role → model, quality vs cost from
`usage.jsonl`.
