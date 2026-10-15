from . import tatc_tools as t

_TLE = {"type": "array", "items": {"type": "string"}, "description": "The two TLE lines."}
_CONSTELLATION = {
    "type": ["object", "null"],
    "description": "Omit for a single satellite; provide for a Walker constellation.",
    "properties": {
        "configuration": {"type": "string", "default": "delta"},
        "number_satellites": {"type": "integer"},
        "number_planes": {"type": "integer"},
    },
}

TOOLS = {
    "revisit_over_point": {
        "fn": t.revisit_over_point,
        "schema": {
            "name": "revisit_over_point",
            "description": "Mean revisit period in hours of a sensor over a ground point, for a single satellite or a Walker constellation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                    "tle": _TLE,
                    "swath_width_m": {"type": "number"},
                    "window_days": {"type": "integer", "default": 30},
                    "constellation": _CONSTELLATION,
                },
                "required": ["latitude", "longitude", "tle", "swath_width_m"],
            },
        },
    },
    "ground_track": {
        "fn": t.ground_track,
        "schema": {
            "name": "ground_track",
            "description": "Sensor ground-track swath polygon (WKT) over a time window in minutes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tle": _TLE,
                    "swath_width_m": {"type": "number"},
                    "window_minutes": {"type": "integer", "default": 30},
                },
                "required": ["tle", "swath_width_m"],
            },
        },
    },
    "global_coverage": {
        "fn": t.global_coverage,
        "schema": {
            "name": "global_coverage",
            "description": "Mean revisit period per point on a global grid at a given spacing (km), for a single satellite or a Walker constellation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tle": _TLE,
                    "swath_width_m": {"type": "number"},
                    "spacing_km": {"type": "number"},
                    "window_days": {"type": "integer", "default": 30},
                    "constellation": _CONSTELLATION,
                },
                "required": ["tle", "swath_width_m", "spacing_km"],
            },
        },
    },
}


def schemas() -> list[dict]:
    return [spec["schema"] for spec in TOOLS.values()]


def names() -> list[str]:
    return list(TOOLS)


def call(name: str, **kwargs):
    return TOOLS[name]["fn"](**kwargs)
