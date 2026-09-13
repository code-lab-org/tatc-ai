#!/usr/bin/env python3
"""Sync the ASU Voyager model list in both librechat.yaml files.

Fetches /models from the ASU proxy, drops non-chat models (embedding,
transcription, speech, image, video), and rewrites the custom endpoint's
default list plus the modelSpecs list to match.

Existing spec entries are preserved by model ID except for known canonical
model metadata overrides. New models get guessed labels and icons for review.
Models that vanished from the proxy are removed. The script refuses to write
an empty list, and drops of more than half the models need --force.

Usage:
    python3 apps/librechat/sync-voyager-models.py [--force]

Then review the diff, restart LibreChat, and check the picker:
    docker compose -f docker-compose.dev.yml restart librechat
"""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
FILES = [BASE / "librechat.yaml", BASE / "librechat.deploy.yaml.template"]
ENV_FILE = BASE / ".env"

# Substrings that mark a model as non-chat. Matched case-insensitively
# against the model ID. Curate in review if a real chat model ever trips one.
NON_CHAT_MARKERS = (
    "embedding",
    "asr",
    "tts",
    "whisper",
    "chatterbox",
    "transcribe",
    "flux-",
    "wan-",
    "image",
    "video",
    "audio",
    "reranker",
    "moderation",
)

META = "https://cdn.jsdelivr.net/npm/simple-icons@v16/icons/meta.svg"
KIMI = "https://raw.githubusercontent.com/MoonshotAI/Branding-Guide/main/scenarios/03-icon-without-kimi/kimi-icon-round.png"
MINIMAX = "https://filecdn.minimax.chat/public/58eca777-e31f-448a-9823-e2220e49b426.png"
QWEN = "/assets/qwen.svg"
GOOGLE = "/assets/google.svg"
OPENAI_ICON = "/assets/openai.svg"
MISTRAL = "/assets/mistral.png"
COHERE = "/assets/cohere.png"
IBM = "https://upload.wikimedia.org/wikipedia/commons/5/51/IBM_logo.svg"
AI2 = "https://github.com/allenai.png"
ZAI = "https://github.com/zai-org.png"
THINKING_MACHINES = "https://github.com/thinkingmachines.png"
POOLSIDE = "https://github.com/poolside-ai.png"
ORNITH = "https://github.com/ornith-ai.png"

# Prefix maps for newly discovered models. Unknown prefixes get no icon
# and a generic description so a human picks the branding in review.
ICON_PREFIXES = [
    ("llama", META),
    ("qwen", QWEN),
    ("gemma", GOOGLE),
    ("gpt-", OPENAI_ICON),
    ("whisper", OPENAI_ICON),
    ("devstral", MISTRAL),
    ("mistral", MISTRAL),
    ("minimax", MINIMAX),
    ("kimi", KIMI),
    ("moonshot", KIMI),
    ("glm", ZAI),
    ("olmo", AI2),
    ("granite", IBM),
    ("muse", META),
    ("north", COHERE),
    ("inkling", THINKING_MACHINES),
    ("laguna", POOLSIDE),
    ("ornith", ORNITH),
]
MAKER_PREFIXES = [
    ("llama", "Meta Llama"),
    ("qwen", "Alibaba Qwen"),
    ("gemma", "Google Gemma"),
    ("gpt-", "OpenAI"),
    ("devstral", "Mistral Devstral"),
    ("mistral", "Mistral"),
    ("minimax", "MiniMax"),
    ("kimi", "Moonshot Kimi"),
    ("moonshot", "Moonshot"),
    ("glm", "Z.ai GLM"),
    ("olmo", "Ai2 Olmo"),
    ("granite", "IBM Granite"),
    ("muse", "Meta Muse"),
    ("north", "Cohere"),
    ("inkling", "Thinking Machines"),
    ("laguna", "Poolside"),
    ("ornith", "Ornith"),
]

WORD_FIXES = {
    "glm": "GLM", "gpt": "GPT", "oss": "OSS", "vl": "VL", "it": "IT",
    "asr": "ASR", "tts": "TTS", "qwen": "Qwen", "llama": "Llama",
    "gemma": "Gemma", "kimi": "Kimi", "minimax": "MiniMax",
    "devstral": "Devstral", "granite": "Granite", "olmo": "Olmo",
    "muse": "Muse", "glimmer": "Glimmer", "north": "North",
    "inkling": "Inkling", "laguna": "Laguna", "ornith": "Ornith",
    "groq": "Groq", "agentworld": "AgentWorld", "coder": "Coder",
    "instruct": "Instruct", "thinking": "Thinking", "think": "Thinking",
    "flash": "Flash", "next": "Next", "small": "Small", "mini": "Mini",
    "code": "Code", "tool": "Tool", "use": "Use", "scout": "Scout",
    "maverick": "Maverick",
}

CANONICAL_MODEL_METADATA = {
    "kimi-k2-7-code": {
        "label": "Kimi K2.7 Code",
        "description": "Moonshot Kimi chat model via ASU Voyager.",
        "icon": KIMI,
    },
    "minimax-m2-7": {
        "label": "MiniMax M2.7",
        "description": "MiniMax chat model via ASU Voyager.",
        "icon": MINIMAX,
    },
    "minimax-m3": {
        "label": "MiniMax M3",
        "description": "MiniMax chat model via ASU Voyager.",
        "icon": MINIMAX,
    },
    "glm-4-5v": {
        "label": "GLM-4.5V",
        "description": "Z.ai GLM chat model via ASU Voyager.",
        "icon": ZAI,
    },
    "glm-5-2": {
        "label": "GLM-5.2",
        "description": "Z.ai GLM chat model via ASU Voyager.",
        "icon": ZAI,
    },
    "glm-5-3": {
        "label": "GLM-5.3",
        "description": "Z.ai GLM chat model via ASU Voyager.",
        "icon": ZAI,
    },
    "glm-5-3-flash": {
        "label": "GLM-5.3-Flash",
        "description": "Z.ai GLM chat model via ASU Voyager.",
        "icon": ZAI,
    },
}


def humanize_token(tok: str) -> str:
    low = tok.lower()
    if low in WORD_FIXES:
        return WORD_FIXES[low]
    if re.fullmatch(r"[a-z]\d+[a-z]*", low):
        return low.upper()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([a-z]*)", low)
    if m:
        num, suffix = m.groups()
        return num + suffix.upper() if suffix else num
    m = re.fullmatch(r"([a-z]{2,})(\d.*)", low)
    if m:
        word, rest = m.groups()
        return f"{WORD_FIXES.get(word, word.capitalize())} {humanize_token(rest)}"
    return tok.capitalize()


def humanize(model_id: str) -> str:
    return " ".join(humanize_token(t) for t in model_id.replace("_", "-").split("-"))


def guess_icon(model_id: str):
    low = model_id.lower()
    for prefix, icon in ICON_PREFIXES:
        if low.startswith(prefix):
            return icon
    return None


def guess_description(model_id: str) -> str:
    low = model_id.lower()
    for prefix, maker in MAKER_PREFIXES:
        if low.startswith(prefix):
            return f"{maker} chat model via ASU Voyager."
    return "Voyager chat model."


def load_env(path: Path) -> dict:
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def fetch_models(base_url: str, api_key: str) -> list:
    req = urllib.request.Request(
        base_url.rstrip("/") + "/models",
        headers={"Authorization": "Bearer " + api_key},
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        data = json.load(resp)
    return sorted(m["id"] for m in data.get("data", []) if m.get("id"))


def is_chat(model_id: str) -> bool:
    low = model_id.lower()
    return not any(mark in low for mark in NON_CHAT_MARKERS)


def build_entry(name, label, desc, icon, model, extra=None) -> str:
    canonical = CANONICAL_MODEL_METADATA.get(model)
    if canonical:
        label = canonical["label"]
        desc = canonical["description"]
        icon = canonical["icon"]

    lines = [
        f"    - name: {name}",
        f"      label: {label}",
        f"      description: {desc}",
        "      group: ASU Voyager",
    ]
    if icon:
        lines += [
            f"      iconURL: {icon}",
            "      showIconInMenu: true",
            "      showIconInHeader: true",
        ]
    for key in ("default", "showOnLanding"):
        if extra and extra.get(key):
            lines.append(f"      {key}: true")
    if extra and extra.get("conversation_starters"):
        lines.append("      conversation_starters:")
        lines += [f"        - {s}" for s in extra["conversation_starters"]]
    lines += [
        "      mcpServers:",
        "        - tatc",
        "      preset:",
        "        endpoint: ASU Voyager",
        f"        model: {model}",
    ]
    if model.lower().startswith("gpt-"):
        lines.append("        modelLabel: ASU Voyager")
    return "\n".join(lines)


def main() -> int:
    import yaml

    force = "--force" in sys.argv
    # Local runs read apps/librechat/.env; CI has no .env file and passes
    # both values as environment instead. Environment wins either way.
    env = dict(os.environ)
    if ENV_FILE.exists():
        env = {**load_env(ENV_FILE), **env}
    missing = [v for v in ("OPENAI_API_KEY", "OPENAI_REVERSE_PROXY") if not env.get(v)]
    if missing:
        print(f"missing required settings: {', '.join(missing)}")
        return 1
    try:
        live = fetch_models(env["OPENAI_REVERSE_PROXY"], env["OPENAI_API_KEY"])
    except Exception as exc:
        print(f"fetch failed: {type(exc).__name__}: {exc}")
        return 1
    if not live:
        print("refusing to write: proxy returned zero models")
        return 1
    live_chat = [m for m in live if is_chat(m)]
    skipped = [m for m in live if not is_chat(m)]

    # Existing specs, keyed by model ID, in file order.
    current = yaml.safe_load(FILES[0].read_text())["modelSpecs"]["list"]
    by_model = {s["preset"]["model"]: s for s in current}
    current_models = [s["preset"]["model"] for s in current]

    added = [m for m in live_chat if m not in by_model]
    removed = [m for m in current_models if m not in live_chat]
    if len(removed) > len(current_models) / 2 and not force:
        print(f"refusing: {len(removed)} of {len(current_models)} specs would vanish. "
              "re-run with --force if the proxy really dropped them.")
        return 1

    blocks = []
    for model in current_models:
        if model in live_chat:
            s = by_model[model]
            extra = {k: s[k] for k in ("default", "showOnLanding",
                                       "conversation_starters") if s.get(k)}
            blocks.append(build_entry(s["name"], s["label"], s["description"],
                                      s.get("iconURL"), model, extra))
    for model in sorted(added):
        blocks.append(build_entry(model, humanize(model),
                                  guess_description(model),
                                  guess_icon(model), model))

    if not any(s["preset"]["model"] in live_chat and s.get("default")
               for s in current) and blocks:
        print("warning: default spec vanished; review the new default")

    specs_block = ("modelSpecs:\n  prioritize: true\n  enforce: false\n  list:\n"
                   + "\n".join(blocks) + "\n")
    ep_lines = ["      models:", "        default:"]
    ep_lines += [f"          - {s['preset']['model']}" for s in
                 yaml.safe_load(specs_block)["modelSpecs"]["list"]]
    ep_lines.append("        fetch: false")
    ep_block = "\n".join(ep_lines)
    ep_re = re.compile(r"      models:\n        default:\n(?:          - .*\n)+"
                       r"        fetch: false\n")

    for path in FILES:
        text = path.read_text()
        assert text.count("modelSpecs:") == 1, path
        head = text.split("modelSpecs:")[0]
        assert ep_re.search(head), f"endpoint block not found in {path}"
        path.write_text(ep_re.sub(ep_block + "\n", head) + specs_block)

    print(f"kept {len(current_models) - len(removed)}, "
          f"added {len(added)}, removed {len(removed)}")
    for m in sorted(added):
        print(f"  + {m} ({humanize(m)})")
    for m in removed:
        print(f"  - {m}")
    if skipped:
        print(f"skipped {len(skipped)} non-chat: {', '.join(skipped)}")
    print("review the diff, then restart librechat to apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
