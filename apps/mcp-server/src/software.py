"""Software versions recorded beside a study for reproducibility."""

import importlib.metadata
import os
import platform
from typing import Dict


def _version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def software_versions() -> Dict[str, str]:
    """Return the versions that can change a study's numerical result."""
    return {
        "server": os.environ.get("TATC_AI_SERVER_VERSION", "dev"),
        "tatc": _version("tatc"),
        "python": platform.python_version(),
        "skyfield": _version("skyfield"),
        "sgp4": _version("sgp4"),
    }
