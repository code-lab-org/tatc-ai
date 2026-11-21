"""Read the LibreChat config files shared by development and deployment."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_FILES = (
    REPO_ROOT / "apps/librechat/librechat.yaml",
    REPO_ROOT / "apps/librechat/librechat.deploy.yaml.template",
)


def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text())
