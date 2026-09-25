import json
from pathlib import Path

import sandbox
from cases import by_tier

OUT = Path(__file__).parent / "ground_truth.json"


def main():
    soldir = Path(__file__).parent / "solutions"
    truth = {}
    for case in by_tier("pinned"):
        code = (soldir / case.solution).read_text()
        ex = sandbox.run(code)
        if not ex.ok:
            raise SystemExit(f"{case.id} failed to build ground truth: {ex.error}")
        truth[case.id] = {"kind": case.kind, "expected": ex.result, "seconds": round(ex.seconds, 1)}
        print(f"{case.id}: built in {ex.seconds:0.1f}s")
    OUT.write_text(json.dumps(truth, indent=2))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
