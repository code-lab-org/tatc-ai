from typing import Optional


def assemble_context(
    task: str,
    examples: Optional[list[str]] = None,
    history: Optional[list[str]] = None,
) -> str:
    parts = list(examples or []) + list(history or [])
    parts.append(task)
    return "\n\n".join(parts)
