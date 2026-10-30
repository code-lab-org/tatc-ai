"""Shared reviewed AMSR2 configuration and derived study-specific fixture."""

import json
from pathlib import Path

SHARED_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "configurations"
    / "amsr2-gcom-w1-conical-product-sector-2026-10-27.1.json"
)


def load_shared_config():
    return json.loads(SHARED_CONFIG_PATH.read_text())


def load_study_config():
    # Eval uses the same derivation in eval/cases.py without importing test code.
    configuration = load_shared_config()
    configuration["configuration_id"] = "amsr2-gcom-w1-conical-product-sector-study"
    configuration["revision"] = "2026-10-27.1-study"
    for check in configuration["geometry_checks"]:
        check.pop("geometry_parameter", None)
    return configuration
