import math
from shapely.geometry import LineString
from ..base import make_issue, rings, safe_geometry, ring_context
ISSUE_TYPE = "SHORT_SEGMENT"


def check(feature_id, geom, attrs, cfg):
    result = []
    if safe_geometry(geom) is None:
        return result
    for part, kind, index, ring in rings(geom):
        coords = list(ring.coords)
        for i, (a, b) in enumerate(zip(coords, coords[1:])):
            distance = math.dist(a[:2], b[:2])
            if cfg.tolerance.distance <= distance < cfg.tolerance.min_segment_length:
                result.append(make_issue(feature_id, ISSUE_TYPE, "WARN", geom=LineString([a, b]), context={**ring_context(part, kind, index), "vertex_index": i, "length": distance}, suggested_fix="merge_or_snap_vertex"))
    return result
