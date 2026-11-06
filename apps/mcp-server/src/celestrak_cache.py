"""Persistent CelesTrak responses and a shared, serialized upstream cooldown."""

import fcntl
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar

import requests

_MAX_BYTES = 50 * 1024 * 1024
_LOG = logging.getLogger(__name__)
_T = TypeVar("_T")


class _ServiceFailure(Exception):
    pass


def _retry_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def cached(
    url: str,
    params: dict[str, str | int],
    ttl: int,
    validate: Callable[[str], _T],
) -> _T:
    """Fetch and validate a response once while holding a cross-process lock."""
    path = Path(
        os.environ.get("CELESTRAK_CACHE_PATH", "~/.cache/tatc-ai/celestrak.sqlite3")
    ).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path) + ".lock", "a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with closing(sqlite3.connect(path, timeout=60)) as db:
                db.execute("PRAGMA journal_mode=DELETE")
                db.execute("PRAGMA max_page_count=15000")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, expires REAL NOT NULL, body BLOB NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS cooldown (id INTEGER PRIMARY KEY CHECK (id=1), until REAL NOT NULL, reason TEXT NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS storage_check (id INTEGER PRIMARY KEY CHECK (id=1), value INTEGER NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS capacity_block (id INTEGER PRIMARY KEY CHECK (id=1), until REAL NOT NULL)"
                )
                key = hashlib.sha256(
                    json.dumps(
                        [url, params], sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest()
                now = time.time()
                row = db.execute(
                    "SELECT body FROM responses WHERE key=? AND expires>?", (key, now)
                ).fetchone()
                if row is not None:
                    try:
                        return validate(row[0].decode("utf-8"))
                    except (ValueError, TypeError, UnicodeError, AttributeError) as exc:
                        raise requests.RequestException(
                            "CelesTrak cache storage contains invalid data"
                        ) from exc
                full = db.execute(
                    "SELECT until FROM capacity_block WHERE id=1"
                ).fetchone()
                if full and full[0] > now:
                    raise requests.RequestException(
                        f"CelesTrak cache is full; retry after {_retry_time(full[0])}."
                    )
                pause = db.execute(
                    "SELECT until, reason FROM cooldown WHERE id=1"
                ).fetchone()
                if pause and pause[0] > now:
                    raise requests.RequestException(
                        f"CelesTrak unavailable: {pause[1]}; retry after {_retry_time(pause[0])}."
                    )
                # Commit reclamation before a fetch so storage failures fail closed.
                db.execute("DELETE FROM responses WHERE expires<=?", (now,))
                db.execute(
                    "INSERT INTO storage_check VALUES (1, 1) ON CONFLICT(id) DO UPDATE SET value=1-value"
                )
                db.commit()
                used = db.execute(
                    "SELECT coalesce(sum(length(body)), 0) FROM responses"
                ).fetchone()[0]
                if used >= _MAX_BYTES:
                    raise requests.RequestException(
                        "CelesTrak cache is full; wait for entries to expire."
                    )
                reason, duration, status, excerpt, headers = None, 0, None, "", {}
                try:
                    response = requests.get(
                        url,
                        params=params,
                        timeout=(10, 30),
                        allow_redirects=False,
                        stream=True,
                    )
                    try:
                        status = response.status_code
                        headers = {
                            name: str(response.headers[name])[:200]
                            for name in ("Content-Type", "Retry-After")
                            if name in response.headers
                        }
                        chunks = []
                        size = 0
                        for chunk in response.iter_content(chunk_size=65536):
                            size += len(chunk)
                            if size > _MAX_BYTES:
                                raise _ServiceFailure(
                                    "response exceeds the 50 MiB limit"
                                )
                            chunks.append(chunk)
                            if status != 200 and size >= 2048:
                                break
                        body = b"".join(chunks)
                        excerpt = (
                            body[:500]
                            .decode("utf-8", errors="replace")
                            .replace("\n", " ")
                            .replace("\r", " ")
                        )
                        if status != 200:
                            lower = (
                                body[:2048].decode("utf-8", errors="replace").lower()
                            )
                            temporary = status == 429 or (
                                status == 403
                                and (
                                    (
                                        "excessive downloads" in lower
                                        and "temporar" in lower
                                        and ("2 hours" in lower or "two hours" in lower)
                                    )
                                    or "gp data has not updated since your last successful download"
                                    in lower
                                )
                            )
                            duration = (
                                7200 if temporary else 600 if status >= 500 else 0
                            )
                            reason = f"HTTP {status}"
                        else:
                            try:
                                text = body.decode("utf-8")
                                result = validate(text)
                            except (ValueError, UnicodeError, TypeError) as exc:
                                raise _ServiceFailure(
                                    f"invalid response: {str(exc)[:200]}"
                                ) from exc
                    finally:
                        response.close()
                except requests.RequestException as exc:
                    reason, duration = type(exc).__name__, 600
                except _ServiceFailure as exc:
                    reason, duration = str(exc), 600
                if reason is not None:
                    now = time.time()
                    until = now + duration
                    if duration:
                        db.execute(
                            "INSERT INTO cooldown VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET until=max(until, excluded.until), reason=CASE WHEN excluded.until > until THEN excluded.reason ELSE reason END",
                            (until, reason),
                        )
                        db.commit()
                    excerpt = re.sub(
                        r"(?i)bearer\s+[^\s<,;\"]+", "Bearer [redacted]", excerpt
                    )
                    excerpt = re.sub(
                        r"(?i)(authorization|token|api[-_ ]?key|password|secret)([\s\"':=]+)[^\s<,;\"]+",
                        r"\1\2[redacted]",
                        excerpt,
                    )
                    _LOG.warning(
                        "CelesTrak failure at %s url=%s params=%s status=%s headers=%s reason=%s excerpt=%r",
                        _retry_time(now),
                        url,
                        str(params)[:500],
                        status,
                        headers,
                        reason,
                        excerpt,
                    )
                    message = f"CelesTrak unavailable: {reason}"
                    if duration:
                        message += f"; retry after {_retry_time(until)}"
                    raise requests.RequestException(message)
                used = db.execute(
                    "SELECT coalesce(sum(length(body)), 0) FROM responses"
                ).fetchone()[0]
                if used + len(body) > _MAX_BYTES:
                    earliest = db.execute(
                        "SELECT min(expires) FROM responses"
                    ).fetchone()[0]
                    if earliest is not None:
                        db.execute(
                            "INSERT OR REPLACE INTO capacity_block VALUES (1, ?)",
                            (earliest,),
                        )
                        db.commit()
                    raise requests.RequestException(
                        "CelesTrak cache is full; wait for entries to expire."
                    )
                db.execute(
                    "INSERT OR REPLACE INTO responses VALUES (?, ?, ?)",
                    (key, time.time() + ttl, body),
                )
                db.commit()
                return result
    except (OSError, sqlite3.Error) as exc:
        raise requests.RequestException(
            f"CelesTrak cache storage unavailable: {exc}"
        ) from exc
