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
from html import unescape
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


def _http_failure(status: int, body: bytes = b"") -> tuple[str, int]:
    text = unescape(
        re.sub(r"<[^>]*>", " ", body[:8192].decode("utf-8", errors="replace"))
    )
    text = " ".join(text.lower().split())
    if status == 429:
        return "HTTP 429: temporary rate limit", 7200
    if status >= 500:
        return f"HTTP {status}: service failure", 600
    if status == 403:
        temporary = (
            "excessive downloads" in text
            and "temporar" in text
            and ("2 hours" in text or "two hours" in text)
        ) or "gp data has not updated since your last successful download" in text
        if temporary:
            return f"HTTP {status}: temporary rate limit", 7200
        return f"HTTP {status}: access denied", 0
    if 300 <= status < 400:
        cause = "unexpected redirect"
    else:
        cause = {
            400: "request rejected",
            401: "access denied",
            404: "requested resource not found",
        }.get(status, "request failed")
    return f"HTTP {status}: {cause}", 0


def _record_activity(db: sqlite3.Connection, key: str, event: str) -> None:
    db.execute(
        "INSERT INTO activity (at, event, query_key) VALUES (?, ?, ?)",
        (time.time(), event, key[:12]),
    )
    db.execute(
        "DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY id DESC LIMIT 64)"
    )
    db.commit()


def cached(
    url: str,
    params: dict[str, str | int],
    ttl: int,
    validate: Callable[[str], _T],
) -> _T:
    """Return fresh cached data or serialize one validated upstream fetch."""
    path = Path(
        os.environ.get("CELESTRAK_CACHE_PATH", "~/.cache/tatc-ai/celestrak.sqlite3")
    ).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(
            json.dumps([url, params], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        row = None
        if path.exists():
            try:
                with closing(
                    sqlite3.connect(
                        path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1
                    )
                ) as reader:
                    row = reader.execute(
                        "SELECT body FROM responses WHERE key=? AND expires>?",
                        (key, time.time()),
                    ).fetchone()
            except sqlite3.Error:
                # A read-only connection cannot recover a hot journal or create
                # missing tables. Fall through to the locked writer, which can.
                row = None
            if row is not None:
                try:
                    result = validate(row[0].decode("utf-8"))
                except (ValueError, TypeError, UnicodeError, AttributeError) as exc:
                    raise requests.RequestException(
                        "CelesTrak cache storage contains invalid data"
                    ) from exc
                try:
                    with closing(sqlite3.connect(path, timeout=0)) as activity_db:
                        _record_activity(activity_db, key, "cache_hit")
                except sqlite3.Error:
                    pass
                return result
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
                    "CREATE TABLE IF NOT EXISTS capacity_failures (key TEXT PRIMARY KEY, until REAL NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY, at REAL NOT NULL, event TEXT NOT NULL, query_key TEXT NOT NULL)"
                )
                now = time.time()
                row = db.execute(
                    "SELECT body FROM responses WHERE key=? AND expires>?", (key, now)
                ).fetchone()
                if row is not None:
                    _record_activity(db, key, "cache_hit")
                    try:
                        return validate(row[0].decode("utf-8"))
                    except (ValueError, TypeError, UnicodeError, AttributeError) as exc:
                        raise requests.RequestException(
                            "CelesTrak cache storage contains invalid data"
                        ) from exc
                full = db.execute(
                    "SELECT until FROM capacity_failures WHERE key=?", (key,)
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
                        f"CelesTrak unavailable: {pause[1]}; downloads paused; retry after {_retry_time(pause[0])}."
                    )
                # Commit reclamation before a fetch so storage failures fail closed.
                db.execute("DELETE FROM responses WHERE expires<=?", (now,))
                db.execute("DELETE FROM capacity_failures WHERE until<=?", (now,))
                db.commit()
                used = db.execute(
                    "SELECT coalesce(sum(length(body)), 0) FROM responses"
                ).fetchone()[0]
                if used >= _MAX_BYTES:
                    raise requests.RequestException(
                        "CelesTrak cache is full; wait for entries to expire."
                    )
                if (
                    db.execute("SELECT count(*) FROM capacity_failures").fetchone()[0]
                    >= 1024
                ):
                    raise requests.RequestException(
                        "CelesTrak cache capacity records are full; wait for entries to expire."
                    )
                _record_activity(db, key, "cache_miss")
                reason, duration, status, excerpt, headers = None, 0, None, "", {}
                chunks: list[bytes] = []
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
                        if status != 200:
                            reason, duration = _http_failure(status)
                        headers = {
                            name: str(response.headers[name])[:200]
                            for name in ("Content-Type", "Retry-After")
                            if name in response.headers
                        }
                        size = 0
                        for chunk in response.iter_content(chunk_size=65536):
                            size += len(chunk)
                            if size > _MAX_BYTES:
                                raise _ServiceFailure(
                                    "response exceeds the 50 MiB limit"
                                )
                            chunks.append(chunk)
                            if status != 200 and size >= 8192:
                                break
                        body = b"".join(chunks)
                        excerpt = (
                            body[:500]
                            .decode("utf-8", errors="replace")
                            .replace("\n", " ")
                            .replace("\r", " ")
                        )
                        if status != 200:
                            reason, duration = _http_failure(status, body)
                        else:
                            try:
                                text = body.decode("utf-8")
                                result = validate(text)
                            except (ValueError, UnicodeError, TypeError) as exc:
                                excerpt = "[invalid response body omitted]"
                                raise _ServiceFailure("invalid response") from exc
                    finally:
                        response.close()
                except requests.RequestException as exc:
                    if status is not None and status != 200:
                        reason, duration = _http_failure(status, b"".join(chunks))
                    else:
                        reason, duration = type(exc).__name__, 600
                except _ServiceFailure as exc:
                    if reason is None:
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
                    _record_activity(db, key, "download_failed")
                    history = db.execute(
                        "SELECT at, event, query_key FROM activity ORDER BY id DESC LIMIT 12"
                    ).fetchall()
                    excerpt = re.sub(
                        r"(?i)bearer\s+[^\s<,;\"]+", "Bearer [redacted]", excerpt
                    )
                    excerpt = re.sub(
                        r"(?i)(authorization|token|api[-_ ]?key|password|secret)([\s\"':=]+)[^\s<,;\"]+",
                        r"\1\2[redacted]",
                        excerpt,
                    )
                    _LOG.warning(
                        "CelesTrak failure at %s url=%s params=%s status=%s headers=%s reason=%s excerpt=%r recent_activity=%s",
                        _retry_time(now),
                        url,
                        str(params)[:500],
                        status,
                        headers,
                        reason,
                        excerpt,
                        history,
                    )
                    message = f"CelesTrak unavailable: {reason}"
                    if duration:
                        message += (
                            f"; downloads paused; retry after {_retry_time(until)}"
                        )
                    else:
                        message += (
                            "; this lookup stopped; no service-wide pause was applied"
                        )
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
                            "INSERT OR REPLACE INTO capacity_failures VALUES (?, ?)",
                            (key, earliest),
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
                _record_activity(db, key, "download_succeeded")
                if pause:
                    db.execute(
                        "DELETE FROM cooldown WHERE id=1 AND until<=?", (time.time(),)
                    )
                    db.commit()
                    _LOG.warning(
                        "CelesTrak service recovered at %s", _retry_time(time.time())
                    )
                return result
    except requests.RequestException:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise requests.RequestException(
            f"CelesTrak cache storage unavailable: {exc}"
        ) from exc
