from itertools import combinations
from shapely import STRtree
from shapely.geometry import LineString
from shapely.validation import explain_validity
from ..base import make_issue, polygons, ring_coords, safe_geometry
ISSUE_TYPE = "SELF_INTERSECTION"


def check(feature_id, geom, attrs, cfg):
    result = []
    if safe_geometry(geom) is None:
        return result
    for part, poly in enumerate(polygons(geom)):
        found = False
        for k, ring in enumerate([poly.exterior, *poly.interiors]):
            coords = ring_coords(ring)
            n = len(coords)
            segments = [LineString([coords[i], coords[(i + 1) % n]]) for i in range(n)]
            if n > cfg.rules.segment_index_threshold:
                tree = STRtree(segments)
                pairs = ((i, int(j)) for i, seg in enumerate(segments) for j in sorted(tree.query(seg)) if i < j)
            else:
                pairs = combinations(range(n), 2)
            for i, j in pairs:
                if j == i + 1 or (i == 0 and j == n - 1):
                    continue
                inter = segments[i].intersection(segments[j])
                if inter.is_empty:
                    continue
                kind = "point" if inter.geom_type == "Point" else "overlap" if inter.geom_type == "LineString" and inter.length > cfg.tolerance.distance else None
                if kind:
                    found = True
                    location_geom = inter if kind == "point" else inter.interpolate(inter.length / (1 + 1))
                    result.append(make_issue(feature_id, ISSUE_TYPE, geom=inter, location=[location_geom.x, location_geom.y], context={"part_index": part, "ring_type": "exterior" if k == 0 else "interior", "ring_index": max(0, k - 1), "segment_i": i, "segment_j": j, "kind": kind}, suggested_fix="make_valid"))
        if not poly.is_valid and not found:
            result.append(make_issue(feature_id, ISSUE_TYPE, message=explain_validity(poly), geom=poly, context={"part_index": part, "kind": "validity_fallback"}, suggested_fix="make_valid"))
    return result
