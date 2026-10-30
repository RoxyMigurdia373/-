import math
from shapely.geometry import Point
from ..base import make_issue, rings, safe_geometry, ring_context
ISSUE_TYPE = "DUPLICATE_VERTEX"


def check(feature_id, geom, attrs, cfg):
    result = []
    if safe_geometry(geom) is None:
        return result
    for part, kind, index, ring in rings(geom):
        coords = list(ring.coords)
        for i, (a, b) in enumerate(zip(coords, coords[1:])):
            if math.dist(a[:2], b[:2]) < cfg.tolerance.distance:
                result.append(make_issue(feature_id, ISSUE_TYPE, "WARN", geom=Point(a[:2]), context={**ring_context(part, kind, index), "vertex_index": i, "coord": a[:2]}, suggested_fix="remove_duplicate_vertex"))
    return result
