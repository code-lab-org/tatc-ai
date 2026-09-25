import argparse
import json
from pathlib import Path

import compare
import sandbox
from cases import by_tier

TRUTH = Path(__file__).parent / "ground_truth.json"


def score(adapter, tier="pinned"):
    truth = json.loads(TRUTH.read_text())
    rows = []
    for case in by_tier(tier):
        if case.kind == "open" or case.id not in truth:
            rows.append({"id": case.id, "status": "unscored", "note": case.notes})
            continue
        code = adapter.solve(case)
        ex = sandbox.run(code)
        if not ex.ok:
            rows.append({"id": case.id, "status": "exec_error", "score": 0.0,
                         "error": ex.error, "seconds": round(ex.seconds, 1)})
            continue
        ok, sc, detail = compare.BY_KIND[case.kind](ex.result, truth[case.id]["expected"], case.tol)
        rows.append({"id": case.id, "status": "pass" if ok else "fail", "score": round(sc, 4),
                     "seconds": round(ex.seconds, 1), "detail": detail})
    scored = [r for r in rows if "score" in r]
    agg = {"adapter": adapter.name, "tier": tier,
           "n": len(scored), "passed": sum(r["status"] == "pass" for r in scored),
           "mean_score": round(sum(r["score"] for r in scored) / len(scored), 4) if scored else 0.0}
    return {"summary": agg, "cases": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default="reference", choices=["reference", "baseline"])
    ap.add_argument("--tier", default="pinned")
    args = ap.parse_args()
    if args.adapter == "reference":
        from adapters import ReferenceAgent as A
    else:
        from adapters import BaselineAgent as A
    print(json.dumps(score(A(), args.tier), indent=2))


if __name__ == "__main__":
    main()
