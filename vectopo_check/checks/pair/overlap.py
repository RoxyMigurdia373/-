from ..base import make_issue, intersections, pair_ids
ISSUE_TYPE = "OVERLAP"
PREDICATE = "intersects"


def check(gdf, candidates, cfg):
    result = []
    for i, j, a, b, inter in intersections(gdf, candidates):
        if inter.area > cfg.tolerance.overlap_area:
            fid, related = pair_ids(gdf, i, j)
            result.append(make_issue(fid, ISSUE_TYPE, geom=inter, context={"overlap_area": inter.area, "overlap_ratio": inter.area / min(a.area, b.area)}, related_feature_id=related, suggested_fix="clip_or_adjust"))
    return result
