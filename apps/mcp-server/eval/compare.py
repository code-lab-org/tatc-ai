from shapely import wkt


def _rel(a, b, tol):
    if b == 0:
        return abs(a) <= tol
    return abs(a - b) / abs(b) <= tol


def scalar(got, expected, tol=0.02):
    ok = _rel(float(got), float(expected), tol)
    return ok, 1.0 if ok else 0.0, {"got": got, "expected": expected}


def vector(got, expected, tol=0.02):
    if len(got) != len(expected):
        return False, 0.0, {"error": "length mismatch", "got": got, "expected": expected}
    hits = [_rel(float(g), float(e), tol) for g, e in zip(got, expected)]
    score = sum(hits) / len(hits)
    return all(hits), score, {"per_element": hits, "got": got, "expected": expected}


def polygon(got, expected, tol=0.98):
    g, e = wkt.loads(got), wkt.loads(expected)
    inter = g.intersection(e).area
    union = g.union(e).area
    iou = inter / union if union else 0.0
    return iou >= tol, iou, {"iou": iou, "threshold": tol}


def table(got, expected, tol=0.02, key="point_id", field="mean_revisit_hr"):
    exp = {r[key]: r[field] for r in expected}
    if {r[key] for r in got} != set(exp):
        return False, 0.0, {"error": "point set mismatch"}
    hits = [_rel(float(r[field]), float(exp[r[key]]), tol) for r in got]
    score = sum(hits) / len(hits)
    return all(hits), score, {"points": len(hits), "passed": sum(hits)}


BY_KIND = {"scalar": scalar, "vector": vector, "polygon": polygon, "table": table}
