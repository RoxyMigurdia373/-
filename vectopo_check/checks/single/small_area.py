from ..base import make_issue, polygons, safe_geometry, geographic_message
ISSUE_TYPE = "SMALL_AREA"


def check(feature_id, geom, attrs, cfg):
    if safe_geometry(geom) is None:
        return []
    return [make_issue(feature_id, ISSUE_TYPE, "WARN", message=geographic_message("面面积小于阈值", cfg), geom=p.representative_point(), context={"area": p.area, "part_index": i}, suggested_fix="merge_or_remove") for i, p in enumerate(polygons(geom)) if p.area < cfg.tolerance.min_area]
