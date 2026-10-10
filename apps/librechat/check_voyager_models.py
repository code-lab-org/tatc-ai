#!/usr/bin/env python3
"""Check that the models TATC AI offers are still served by ASU Voyager.

TATC AI offers a short, hand-picked list of models (see modelSpecs in
librechat.yaml). This script compares that list with the proxy's /models
listing and reports:

- offered models the proxy no longer serves, which break the picker, and
- chat models the proxy serves that TATC AI does not offer, for review.

It never edits the config: adding a model is a deliberate choice, made by
copying a modelSpecs entry in both librechat files. Exits 1 when an
offered model is missing.

Usage:
    python3 apps/librechat/check_voyager_models.py [--summary PATH]

--summary writes a Markdown report, e.g. to $GITHUB_STEP_SUMMARY.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
FILES = [BASE / "librechat.yaml", BASE / "librechat.deploy.yaml.template"]
ENV_FILE = BASE / ".env"

# Substrings that mark a proxy model as non-chat, so it is never suggested.
NON_CHAT_MARKERS = (
    "embedding", "asr", "tts", "whisper", "chatterbox", "transcribe",
    "flux-", "wan-", "image", "video", "audio", "reranker", "moderation",
    "router-", "classifier",
)


def load_env(path: Path) -> dict:
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def fetch_models(base_url: str, api_key: str) -> list:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/models",
        headers={"Authorization": "Bearer " + api_key},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        data = json.load(response)
    return sorted(m["id"] for m in data.get("data", []) if m.get("id"))


def offered_models(path: Path) -> list:
    import yaml

    specs = yaml.safe_load(path.read_text())["modelSpecs"]["list"]
    return [spec["preset"]["model"] for spec in specs]


def main() -> int:
    argv = sys.argv[1:]
    summary_path = Path(argv[argv.index("--summary") + 1]) if "--summary" in argv else None

    # Local runs read apps/librechat/.env; CI passes both values as environment.
    env = dict(os.environ)
    if ENV_FILE.exists():
        env = {**load_env(ENV_FILE), **env}
    missing_settings = [v for v in ("OPENAI_API_KEY", "OPENAI_REVERSE_PROXY") if not env.get(v)]
    if missing_settings:
        print(f"missing required settings: {', '.join(missing_settings)}")
        return 1

    offered = offered_models(FILES[0])
    if offered_models(FILES[1]) != offered:
        print("librechat.yaml and librechat.deploy.yaml.template offer different models")
        return 1
    live = fetch_models(env["OPENAI_REVERSE_PROXY"], env["OPENAI_API_KEY"])
    gone = [m for m in offered if m not in live]
    available = [
        m for m in live
        if m not in offered and not any(mark in m.lower() for mark in NON_CHAT_MARKERS)
    ]

    lines = [f"Offered: {', '.join(offered)}"]
    lines.append(f"No longer served: {', '.join(gone)}" if gone else "All offered models are served.")
    if available:
        lines.append(f"Also available, not offered: {', '.join(available)}")
    print("\n".join(lines))
    if summary_path:
        summary_path.write_text("## ASU Voyager models\n\n" + "\n".join(f"- {l}" for l in lines) + "\n")
    return 1 if gone else 0


if __name__ == "__main__":
    sys.exit(main())
