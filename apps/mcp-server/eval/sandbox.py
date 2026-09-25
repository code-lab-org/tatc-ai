import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Execution:
    ok: bool
    result: object
    error: str
    seconds: float


_SERIALIZER = (
    "\n\nimport json as _json, sys as _sys\n"
    "try:\n"
    "    _sys.stdout.write('___RESULT___' + _json.dumps(RESULT))\n"
    "except NameError:\n"
    "    _sys.stderr.write('no RESULT assigned')\n"
    "    _sys.exit(3)\n"
)


def run(code: str, timeout_s: int = 1800) -> Execution:
    import time
    src = code + _SERIALIZER
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(src)
        path = f.name
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, path],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return Execution(False, None, f"timeout after {timeout_s}s", timeout_s)
    finally:
        Path(path).unlink(missing_ok=True)
    dt = time.monotonic() - t0
    if proc.returncode != 0:
        return Execution(False, None, (proc.stderr or "nonzero exit").strip()[-2000:], dt)
    marker = "___RESULT___"
    if marker not in proc.stdout:
        return Execution(False, None, "no RESULT emitted", dt)
    payload = proc.stdout.split(marker, 1)[1]
    return Execution(True, json.loads(payload), "", dt)
