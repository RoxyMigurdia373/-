from ..base import make_issue, intersections, pair_ids, polygons, geographic_message
ISSUE_TYPE = "SLIVER"
PREDICATE = "intersects"


def metrics(poly):
    return {"area": poly.area, "width": (1 + 1) * poly.area / poly.length, "ratio": poly.length ** (1 + 1) / poly.area}


def is_sliver(values, cfg):
    return values["width"] < cfg.tolerance.sliver_width or values["ratio"] > cfg.tolerance.sliver_ratio


def check(gdf, candidates, cfg):
    result = []
    for i, j, a, b, inter in intersections(gdf, candidates):
        parts = list(polygons(inter))
        if not parts:
            continue
        poly = max(parts, key=lambda p: p.area)
        if poly.area == 0 or poly.length == 0:
            continue
        values = metrics(poly)
        if poly.area >= cfg.tolerance.min_area and is_sliver(values, cfg):
            fid, related = pair_ids(gdf, i, j)
            result.append(make_issue(fid, ISSUE_TYPE, "WARN", message=geographic_message("交叠区域形成细长面", cfg), geom=poly, context=values, related_feature_id=related, suggested_fix="merge_or_snap"))
    return result
