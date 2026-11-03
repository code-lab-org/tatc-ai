"""Draw a static ground-track picture with cartopy, like the docs/ notebooks."""

import io
from typing import Any, Dict, List

import matplotlib

matplotlib.use("Agg")  # no display in the server container

import cartopy.crs as ccrs  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402


# LibreChat's dark theme colors, so the picture sits naturally in the chat.
BACKGROUND = "#212121"
OCEAN = "#2f2f2f"
TEXT = "#ececec"
COAST = "#999696"
# Drawn at twice the width it is usually shown at, so it stays sharp when scaled.
DPI = 200


def render_static_map_png(title: str, messages: List[Dict[str, Any]]) -> bytes:
    """Return a PNG world map with the ground track drawn on it."""
    lats = [m["position_lla"]["lat_deg"] for m in messages]
    lons = [m["position_lla"]["lon_deg"] for m in messages]

    fig, ax = plt.subplots(
        figsize=(8, 5), facecolor=BACKGROUND,
        subplot_kw={"projection": ccrs.PlateCarree(), "facecolor": OCEAN})
    ax.coastlines(color=COAST)
    ax.set_global()
    # Geodetic draws each segment the short way, so antimeridian crossings
    # do not streak across the map.
    ax.plot(lons, lats, color="#f06595", transform=ccrs.Geodetic())
    ax.plot(lons[0], lats[0], "o", color="#51cf66", transform=ccrs.PlateCarree(),
            label=f"Start {messages[0]['time']}")
    ax.plot(lons[-1], lats[-1], "o", color="#ff8787", transform=ccrs.PlateCarree(),
            label=f"End {messages[-1]['time']}")
    ax.legend(loc="lower left", fontsize=8, facecolor=OCEAN, edgecolor=COAST, labelcolor=TEXT)
    ax.set_title(title, color=TEXT)

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return buffer.getvalue()
