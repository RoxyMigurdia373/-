from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
from ..checks.base import safe_geometry
from .base import reject, validate_candidate


def repair(feature_id, geom, attrs, cfg):
    if cfg.repair.hole_action == "cut_open":
        from .cut_open import repair as cut
        return cut(feature_id, geom, attrs, cfg)
    if safe_geometry(geom) is None or geom.geom_type not in {"Polygon", "MultiPolygon"}:
        return reject(feature_id, geom, "仅支持坐标有限的 Polygon / MultiPolygon")
    parts = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    changed, structural, rebuilt = False, False, []
    for poly in parts:
        shell = Polygon(poly.exterior)
        if not shell.is_valid:
            return reject(feature_id, geom, "外环无效，需人工重建", "rebuild_invalid_holes")
        holes = [Polygon(r) for r in poly.interiors]
        if any(not h.is_valid for h in holes):
            return reject(feature_id, geom, "洞环自身无效，需人工重建", "rebuild_invalid_holes")
        outside = any(not shell.covers(h) for h in holes)
        overlapping = any(a.intersection(b).area > 0 for i, a in enumerate(holes) for b in holes[i + 1:])
        if outside or overlapping:
            # Clip hole area to the shell; never silently choose a largest polygon.
            candidate = shell.difference(unary_union(holes))
            if candidate.geom_type != "Polygon":
                return reject(feature_id, geom, "越界洞重建将产生多部件或非面，已回退", "rebuild_invalid_holes")
            changed, structural = True, True
            rebuilt.append(candidate)
            continue
        kept = []
        for hole in holes:
            limit = cfg.repair.auto_fill_max_area
            fill = limit is not None and hole.area <= limit
            if cfg.repair.fill_if_same_class:
                context, field = cfg._repair_context, cfg.repair.class_field
                neighbors = [] if context is None else context.neighbors(hole, str(feature_id))
                adjacent = [(g, a) for _, g, a in neighbors if g.boundary.intersection(hole.boundary).length > 0]
                # Same class alone never grants an unbounded area fill.
                fill = fill and bool(field and attrs.get(field) is not None and adjacent)
                fill = fill and all(a.get(field) == attrs.get(field) for _, a in adjacent)
            if fill:
                changed = True
            else:
                kept.append(list(hole.exterior.coords))
        rebuilt.append(Polygon(poly.exterior, kept))
    if not changed:
        return reject(feature_id, geom, "未满足填充条件；自动填充默认关闭")
    candidate = rebuilt[0] if geom.geom_type == "Polygon" else MultiPolygon(rebuilt)
    return validate_candidate(feature_id, geom, candidate, attrs, cfg,
                              "rebuild_invalid_holes" if structural else "fill_holes",
                              "pending_review" if structural else "auto_accepted",
                              "非法洞结构重建必须人工确认" if structural else "满足洞面积及分类条件")
