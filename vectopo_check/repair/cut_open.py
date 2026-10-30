from shapely.geometry import Polygon, MultiPolygon, LineString
from shapely.ops import nearest_points, unary_union
from ..checks.base import safe_geometry
from .base import reject, result


def repair(feature_id, geom, attrs, cfg):
    action = "cut_open_holes"
    if cfg.is_geographic:
        return reject(feature_id, geom, "切洞必须在米制投影下执行", action)
    if safe_geometry(geom) is None or geom.geom_type not in {"Polygon", "MultiPolygon"}:
        return reject(feature_id, geom, "不支持该几何", action)
    parts = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    if not any(p.interiors for p in parts):
        return reject(feature_id, geom, "没有岛洞，无需切开")
    output = []
    for poly in parts:
        shell = Polygon(poly.exterior)
        holes = [Polygon(r) for r in poly.interiors]
        if not shell.is_valid or any(not h.is_valid or not shell.covers(h) for h in holes):
            return reject(feature_id, geom, "外环或洞环非法/越界，需人工重建", action)
        cut = shell.difference(unary_union(holes)) if holes else poly
        while cut.geom_type == "Polygon" and len(cut.interiors):
            before = len(cut.interiors)
            ring = cut.interiors[0]
            nearest = nearest_points(cut.exterior, ring)
            # A shortest cut can sever a narrow neck. Try alternative points
            # on the same hole before giving up; never fill it or discard parts.
            opened = None
            for index in range(17):
                if index == 0:
                    a, b = nearest
                else:
                    b = ring.interpolate((index - 1) / 16, normalized=True)
                    a = nearest_points(cut.exterior, b)[0]
                connector = a if a.equals(b) else LineString([a, b])
                channel = connector.buffer(cfg.repair.cut_width / 2)
                candidate_cut = cut.difference(channel)
                if candidate_cut.geom_type == "Polygon" and candidate_cut.is_valid and len(candidate_cut.interiors) < before:
                    opened = candidate_cut
                    break
            if opened is None:
                return reject(feature_id, geom, "切开后产生多部件或洞未减少，已回退", action)
            cut = opened
        if cut.geom_type != "Polygon" or not cut.is_valid:
            return reject(feature_id, geom, "切开后几何不合法，已回退", action)
        output.append(cut)
    candidate = output[0] if geom.geom_type == "Polygon" else MultiPolygon(output)
    if candidate.is_empty or not candidate.is_valid:
        return reject(feature_id, geom, "切开后整体几何不合法，已回退", action)
    loss = geom.area - candidate.area
    protected = abs(loss) > cfg.repair.touch_max_area_change or abs(loss) / max(abs(geom.area), cfg.tolerance.distance) > cfg.repair.touch_max_area_ratio
    res = result(feature_id, geom, candidate, action, "pending_review" if protected else "auto_accepted",
                 "切口面积变化超过保护阈值，需复核" if protected else "窄通道连接洞与外边界；未填充洞内区域")
    res.diff.update(cut_width=cfg.repair.cut_width, removed_area=loss)
    return res
