from typing import Optional


def assemble_context(
    task: str,
    history: Optional[list[str]] = None,
    memories: Optional[list[str]] = None,
) -> str:
    parts = list(memories or []) + list(history or [])
    parts.append(task)
    return "\n\n".join(parts)
