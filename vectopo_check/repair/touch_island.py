from itertools import combinations
from shapely.geometry import Polygon, MultiPolygon, LineString
from ..checks.base import safe_geometry
from .base import reject, validate_candidate


def repair(feature_id, geom, attrs, cfg):
    if cfg.repair.hole_action == "cut_open":
        from .cut_open import repair as cut
        return cut(feature_id, geom, attrs, cfg)
    action = "remove_touching_hole"
    if safe_geometry(geom) is None or geom.geom_type not in {"Polygon", "MultiPolygon"}:
        return reject(feature_id, geom, "不支持该几何", action)
    parts = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    rebuilt, removed = [], 0
    for poly in parts:
        exterior = LineString(poly.exterior.coords)
        holes = [LineString(r.coords) for r in poly.interiors]
        remove = {i for i, hole in enumerate(holes) if exterior.intersects(hole)}
        for i, j in combinations(range(len(holes)), 2):
            if holes[i].intersects(holes[j]):
                # Remove the smaller hole; stable index breaks equal-area ties.
                remove.add(min((i, j), key=lambda k: (Polygon(holes[k]).area, k)))
        removed += len(remove)
        rebuilt.append(Polygon(poly.exterior, [r.coords for i, r in enumerate(poly.interiors) if i not in remove]))
    if not removed:
        return reject(feature_id, geom, "没有实际接触洞；仅距离接近不自动处理", action)
    candidate = rebuilt[0] if geom.geom_type == "Polygon" else MultiPolygon(rebuilt)
    delta = abs(candidate.area - geom.area)
    ratio = delta / abs(geom.area) if geom.area else (float("inf") if delta else 0)
    protected = delta > cfg.repair.touch_max_area_change or ratio > cfg.repair.touch_max_area_ratio
    return validate_candidate(feature_id, geom, candidate, attrs, cfg, action,
                              "pending_review" if protected else "auto_accepted",
                              "面积变化超过保护阈值，强制复核" if protected else "面积变化在保护范围内")
