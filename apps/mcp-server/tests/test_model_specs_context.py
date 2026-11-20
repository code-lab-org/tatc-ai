"""Every ASU model spec keeps a usable context budget for the TATC tools.

LibreChat does not recognize the ASU model IDs, so without maxContextTokens it
uses a 32,000-token default and keeps 5% in reserve.  The TATC tool definitions
alone are close to that, which left no room for the user's message.
"""

import yaml
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_FILES = (
    REPO_ROOT / "apps/librechat/librechat.yaml",
    REPO_ROOT / "apps/librechat/librechat.deploy.yaml.template",
)
# The smallest real window among the models offered through ASU.
MIN_CONTEXT_TOKENS = 100_000


def _asu_specs(path: Path) -> dict[str, dict]:
    config = yaml.safe_load(path.read_text())
    return {
        spec["name"]: spec["preset"]
        for spec in config["modelSpecs"]["list"]
        if spec["preset"].get("endpoint", "").startswith("ASU ")
    }


def test_every_asu_spec_sets_a_context_window():
    for path in CONFIG_FILES:
        for name, preset in _asu_specs(path).items():
            assert preset.get("maxContextTokens", 0) >= MIN_CONTEXT_TOKENS, (
                f"{path.name} spec {name!r} leaves LibreChat's 32,000-token default, "
                "which the TATC tool definitions almost fill"
            )


def test_development_and_deployment_files_agree_on_asu_specs():
    development, deployment = (_asu_specs(path) for path in CONFIG_FILES)
    assert development.keys() == deployment.keys()
    for name in development:
        assert development[name] == deployment[name], name
