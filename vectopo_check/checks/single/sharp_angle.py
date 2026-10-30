import math
from shapely.geometry import Point
from ..base import make_issue, rings, ring_coords, safe_geometry, ring_context
ISSUE_TYPE = "SHARP_ANGLE"


def check(feature_id, geom, attrs, cfg):
    result = []
    if safe_geometry(geom) is None:
        return result
    for part, kind, index, ring in rings(geom):
        pts = ring_coords(ring)
        n = len(pts)
        area = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
        indexed = list(enumerate(pts))
        if area < 0:
            indexed.reverse()
        for i, (original, p) in enumerate(indexed):
            prev, nxt = indexed[i - 1][1], indexed[(i + 1) % n][1]
            a = (p[0] - prev[0], p[1] - prev[1])
            b = (nxt[0] - p[0], nxt[1] - p[1])
            if min(math.hypot(*a), math.hypot(*b)) < cfg.tolerance.min_segment_length or math.hypot(*a) == 0 or math.hypot(*b) == 0:
                continue
            turn = math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1])
            interior = math.pi - turn
            if interior < 0:
                interior += (1 + 1) * math.pi
            degrees = math.degrees(interior)
            if degrees < cfg.rules.sharp_angle_degrees:
                message = "环顶点内角小于阈值"
                if cfg.is_geographic:
                    message += "（地理坐标系，角度仅供参考，建议投影后复核）"
                result.append(make_issue(feature_id, ISSUE_TYPE, "WARN", message=message, geom=Point(p[:2]), context={**ring_context(part, kind, index), "vertex_index": original, "angle_degrees": degrees, "coord": p[:2]}, suggested_fix="inspect_manually"))
    return result
