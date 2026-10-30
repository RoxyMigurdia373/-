from itertools import combinations
from shapely import STRtree
from shapely.geometry import LineString
from shapely.ops import nearest_points
from ..base import make_issue, polygons, safe_geometry
ISSUE_TYPE = "TOUCH_ISLAND"


def first_point(geom):
    if geom.geom_type == "Point":
        return [geom.x, geom.y]
    if hasattr(geom, "geoms"):
        return first_point(next(g for g in geom.geoms if not g.is_empty))
    return list(geom.coords[0][:2])


def check(feature_id, geom, attrs, cfg):
    result = []
    if not any(len(poly.interiors) for poly in polygons(geom)):
        return result
    if safe_geometry(geom) is None:
        return result
    for part, poly in enumerate(polygons(geom)):
        exterior = LineString(poly.exterior.coords)
        holes = [LineString(r.coords) for r in poly.interiors]
        pairs = [(exterior, h, "hole_exterior", {"hole_index": i}) for i, h in enumerate(holes)]
        tree = STRtree(holes)
        pairs += [(holes[i], holes[int(j)], "hole_hole", {"hole_index_i": i, "hole_index_j": int(j)})
                  for i, hole in enumerate(holes)
                  for j in sorted(tree.query(hole, predicate="dwithin", distance=cfg.tolerance.distance)) if i < j]
        for a, b, kind, context in pairs:
            inter = a.intersection(b)
            distance = a.distance(b)
            if inter.is_empty and distance > cfg.tolerance.distance:
                continue
            exact = not inter.is_empty
            point = first_point(inter) if exact else list(nearest_points(a, b)[0].coords[0][:2])
            result.append(make_issue(feature_id, ISSUE_TYPE, "ERROR" if exact else "WARN", geom=inter if exact else None, location=point, context={"part_index": part, "touch_type": kind if exact else "near_" + kind, **context, **({} if exact else {"distance": distance})}, suggested_fix="make_valid_or_remove_shared_edge"))
    return result
