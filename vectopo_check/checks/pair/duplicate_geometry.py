from ..base import make_issue, pair_ids, safe_geometry
ISSUE_TYPE = "DUPLICATE_GEOMETRY"
PREDICATE = "intersects"


def check(gdf, candidates, cfg):
    result = []
    geoms = getattr(gdf, "_vectopo_geometries", None)
    if geoms is None:
        geoms = gdf.geometry.array
    for i, j in candidates:
        a, b = geoms[i], geoms[j]
        if safe_geometry(a) is None or safe_geometry(b) is None:
            continue
        if a.equals_exact(b, tolerance=cfg.tolerance.distance) or a.equals(b):
            fid, related = pair_ids(gdf, i, j)
            result.append(make_issue(fid, ISSUE_TYPE, geom=a, related_feature_id=related, suggested_fix="remove_duplicate"))
    return result
