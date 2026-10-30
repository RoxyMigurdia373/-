from itertools import combinations
from shapely import STRtree
from ..base import make_issue, safe_geometry
ISSUE_TYPE = "MULTIPART"


def check(feature_id, geom, attrs, cfg):
    if geom is None or geom.geom_type not in {"MultiPolygon", "GeometryCollection"} or safe_geometry(geom) is None:
        return []
    parts = [g for g in geom.geoms if g.geom_type == "Polygon" and not g.is_empty]
    if len(parts) <= 1:
        return []
    if len(parts) <= cfg.rules.multipart_direct_threshold:
        distance = min(a.distance(b) for a, b in combinations(parts, 2))
    else:
        # Excluding by geometry equality would incorrectly discard identical
        # parts; filter by row index instead and preserve zero distances.
        distance = min(
            float(STRtree(parts[:i] + parts[i + 1:]).query_nearest(p, return_distance=True)[1].min())
            for i, p in enumerate(parts)
        )
    return [make_issue(feature_id, ISSUE_TYPE, cfg.rules.multipart_policy.upper(), geom=geom.representative_point(),
                       context={"part_count": len(parts), "part_areas": [p.area for p in parts], "min_interpart_distance": distance}, suggested_fix="keep_or_split")]
