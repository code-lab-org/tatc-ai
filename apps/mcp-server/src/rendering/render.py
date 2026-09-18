import json


def to_markdown_table(rows: list[dict]) -> str:
    headers = list(rows[0].keys())
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")
    return "\n".join(lines)


def render(text: str) -> str:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    if isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
        return to_markdown_table(data)
    return text
