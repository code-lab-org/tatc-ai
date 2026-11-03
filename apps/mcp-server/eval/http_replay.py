"""Record and replay CelesTrak and OSCAR HTTP responses for the eval.

Every eval run goes through the real client code, but each URL is fetched
live only once and then replayed from fixtures/http_cache.json. That keeps
runs comparable across models and days: every model sees the same orbit
elements and OSCAR pages. The cache also stores the time it was first
recorded, which the eval uses as "now" so the OMM freshness checks keep
passing when the cache is replayed later.
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests

CACHE_PATH = Path(__file__).resolve().parent / "fixtures" / "http_cache.json"


class HttpReplay:
    """Patch requests.get to replay recorded responses, recording misses."""

    def __init__(self, path: Path = CACHE_PATH, offline: bool = False):
        self.path = path
        self.offline = offline
        self._lock = threading.Lock()
        data = json.loads(path.read_text()) if path.exists() else {}
        self.entries: Dict[str, Dict[str, Any]] = data.get("entries", {})
        self.recorded_at = datetime.fromisoformat(
            data.get("recorded_at")
            or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        )
        self.misses = 0
        self._real_get = requests.get

    @staticmethod
    def _key(url: str, params: Optional[Dict[str, Any]]) -> str:
        if not params:
            return url
        return url + "?" + urlencode(sorted((k, str(v)) for k, v in params.items()))

    def get(self, url: str, params: Optional[Dict[str, Any]] = None, **kwargs: Any):
        key = self._key(url, params)
        with self._lock:
            entry = self.entries.get(key)
        if entry is None:
            if self.offline:
                raise requests.ConnectionError(f"Not recorded for offline eval: {key}")
            live = self._real_get(url, params=params, **kwargs)
            entry = {"status": live.status_code, "text": live.text}
            with self._lock:
                self.entries[key] = entry
                self.misses += 1
        response = requests.Response()
        response.status_code = entry["status"]
        response._content = entry["text"].encode("utf-8")
        response.encoding = "utf-8"
        response.url = key
        return response

    def install(self) -> None:
        # Both clients call requests.get through the shared module object.
        requests.get = self.get  # type: ignore[assignment]

    def save(self) -> None:
        if not self.misses:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            payload = {
                "recorded_at": self.recorded_at.isoformat(),
                "entries": dict(sorted(self.entries.items())),
            }
        self.path.write_text(json.dumps(payload, indent=1) + "\n")
