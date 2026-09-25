from dataclasses import dataclass, field


@dataclass
class Case:
    id: str
    tier: str
    kind: str
    question: str
    solution: str
    tol: float = 0.02
    notes: str = ""


CASES = [
    Case("Q1", "pinned", "scalar",
         "What is the mean revisit period over Tempe Arizona for the VIIRS instrument onboard NOAA 20?",
         "Q1.py"),
    Case("Q2", "pinned", "scalar",
         "What is the mean revisit period over Tempe Arizona for a VIIRS instrument with a Walker Delta "
         "constellation with 3 satellites in 3 planes following the orbit of NOAA 20?",
         "Q2.py"),
    Case("Q3", "pinned", "vector",
         "How does mean revisit period over Tempe Arizona change with 1-6 satellites per plane for a VIIRS "
         "instrument onboard a Walker Delta constellation with 3 planes following the orbit of NOAA 20?",
         "Q3.py"),
    Case("Q4", "pinned", "polygon",
         "What is the ground track for the VIIRS instrument onboard NOAA 20 over a 30-minute period?",
         "Q4.py", tol=0.98),
    Case("Q5", "pinned", "table",
         "Perform a global coverage analysis with mean revisit period for the VIIRS instrument onboard NOAA 20 "
         "for sample points equally spaced at 5000 km.",
         "Q5.py"),
    Case("SNOW", "live", "open",
         "What is the observable region from AMSR2 (GCOM-W, descending only), SAR-C (Sentinel-1A), "
         "SAR-L (NISAR) and ATLAS (ICESat-2) integrated at 1-day intervals for a calendar year?",
         "", notes="No reference answer; agentic stretch case requiring data sourcing and orbit setup."),
]


def by_tier(tier):
    return [c for c in CASES if c.tier == tier]


def get(cid):
    return next(c for c in CASES if c.id == cid)
