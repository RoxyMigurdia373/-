import math
from ..base import make_issue, rings
ISSUE_TYPE = "INVALID_COORDINATE"


def check(feature_id, geom, attrs, cfg):
    t = cfg.tolerance
    limits = (t.longitude_limit, t.latitude_limit) if cfg.is_geographic else (t.projected_coordinate_limit,) * 2
    for *_, ring in rings(geom):
        for coord in ring.coords:
            xy = coord[:2]
            reason = ("nan" if any(math.isnan(v) for v in xy) else
                      "inf" if any(math.isinf(v) for v in xy) else
                      "out_of_range" if any(abs(v) > limit for v, limit in zip(xy, limits)) else None)
            if reason:
                return [make_issue(feature_id, ISSUE_TYPE, context={"coord": xy, "reason": reason}, suggested_fix="remove_or_fix_coord")]
    return []
