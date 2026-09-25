import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..models.base import LLMResponse

_LOCK = threading.Lock()


def log_path() -> Path:
    return Path(os.environ.get("USAGE_LOG_PATH", "usage.jsonl"))


def record(task: str, response: LLMResponse, extra: Optional[dict] = None) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task": task,
        "model": response.model,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "latency_s": round(response.latency_s, 3),
    }
    if extra:
        entry.update(extra)
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
