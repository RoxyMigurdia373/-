from shapely.ops import unary_union
from ..base import make_issue, polygons, safe_geometry, geographic_message
from .sliver import metrics, is_sliver
ISSUE_TYPE = "GAP"
ISSUE_TYPES = ("GAP", "SLIVER")
MULTI_ISSUE = True
PREDICATE = None


def check(gdf, candidates, cfg):
    parts = [p for geom in gdf.geometry if safe_geometry(geom) is not None for p in polygons(geom) if p.is_valid]
    if not parts:
        return []
    covered = unary_union(parts)
    if covered.is_empty:
        return []
    if not covered.is_valid:
        return [make_issue("__gap__", ISSUE_TYPE, message="覆盖区域无效，跳过缝隙运算；未修复输入", context={"error": "invalid union", "extent_method": "convex_hull"})]
    extent = covered.convex_hull
    result = []
    for poly in polygons(extent.difference(covered)):
        if poly.area <= cfg.tolerance.min_area or poly.length == 0:
            continue
        values = metrics(poly)
        thin = is_sliver(values, cfg)
        result.append(make_issue("__gap__", "SLIVER" if thin else ISSUE_TYPE, "WARN", message=geographic_message("凸包范围内存在细长空隙" if thin else "凸包范围内存在覆盖空隙", cfg), geom=poly, context={**values, "extent_method": "convex_hull", "source": "gap"}, suggested_fix="fill_or_adjust_boundary"))
    return result
