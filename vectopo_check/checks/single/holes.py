from itertools import combinations
from shapely.geometry import Polygon
from shapely.validation import explain_validity
from ..base import make_issue, polygons, safe_geometry
ISSUE_TYPE = "HAS_HOLE"
ISSUE_TYPES = ("HAS_HOLE", "INVALID_HOLE")
MULTI_ISSUE = True


def check(feature_id, geom, attrs, cfg):
    result = []
    parts = [poly for poly in polygons(geom) if len(poly.interiors)]
    if not parts:
        return result
    if safe_geometry(geom) is None:
        return result
    for part, poly in enumerate(polygons(geom)):
        if not poly.interiors:
            continue
        holes = [Polygon(r) for r in poly.interiors]
        result.append(make_issue(feature_id, ISSUE_TYPE, cfg.rules.hole_policy.upper(), geom=poly.representative_point(),
                                 context={"part_index": part, "hole_count": len(holes), "hole_areas": [h.area for h in holes]}, suggested_fix="confirm_if_intentional"))
        if cfg._active_rules is not None and "INVALID_HOLE" not in cfg._active_rules:
            continue
        found = False
        def invalid(context, geometry, message="非法洞结构"):
            nonlocal found
            found = True
            result.append(make_issue(feature_id, "INVALID_HOLE", message=message, geom=geometry,
                                     context={"part_index": part, **context}, suggested_fix="make_valid"))
        exterior = Polygon(poly.exterior)
        for i, (ring, hole) in enumerate(zip(poly.interiors, holes)):
            if ring.coords[0] != ring.coords[-1]:
                invalid({"hole_index": i, "reason": "unclosed_ring"}, hole)
            if not exterior.contains(hole):
                invalid({"hole_index": i, "reason": "hole_outside_exterior"}, hole)
        for i, j in combinations(range(len(holes)), 2):
            inter = holes[i].intersection(holes[j])
            if not inter.is_empty and inter.area > 0:
                invalid({"hole_index_i": i, "hole_index_j": j, "reason": "holes_overlap"}, inter)
        explanation = explain_validity(poly)
        if not found and any(word in explanation.lower() for word in ("hole", "interior")):
            invalid({"reason": "validity", "detail": explanation}, poly, explanation)
    return result
