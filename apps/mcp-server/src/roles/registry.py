from typing import Optional

from .role import Role

DEFAULT_ROLE = "user"

ANALYSIS_TOOLS = ("revisit_over_point", "ground_track", "global_coverage")

ROLES: dict[str, Role] = {
    "user": Role(
        name="user",
        tools=ANALYSIS_TOOLS,
        capabilities=frozenset({"query"}),
    ),
    "contributor": Role(
        name="contributor",
        tools=ANALYSIS_TOOLS,
        capabilities=frozenset({"query", "author"}),
    ),
    "developer": Role(
        name="developer",
        tools=ANALYSIS_TOOLS,
        capabilities=frozenset({"query", "author", "run_eval"}),
    ),
    "admin": Role(
        name="admin",
        tools=ANALYSIS_TOOLS,
        capabilities=frozenset({"query", "author", "run_eval", "promote", "manage_users"}),
    ),
}


def get(name: Optional[str] = None) -> Role:
    return ROLES[name or DEFAULT_ROLE]


def capabilities(name: str) -> frozenset:
    return get(name).capabilities
