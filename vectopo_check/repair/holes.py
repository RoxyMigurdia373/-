from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
from ..checks.base import safe_geometry
from .base import reject, result, validate_candidate


def repair(feature_id, geom, attrs, cfg):
    action = cfg.repair.hole_action
    if action == "split_open":
        # Holes were already cut open by the zero-width pre-pass; anything left
        # keeps its hole on purpose so no area is silently moved.
        return reject(feature_id, geom, "零宽切分已处理，剩余洞保持原样", "none")
    if action in {"cut_open", "slit"}:
        from .cut_open import repair as cut
        return cut(feature_id, geom, attrs, cfg)
    if action == "fill":
        return fill_all(feature_id, geom, attrs, cfg)
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


def fill_all(feature_id, geom, attrs, cfg):
    """Absorb every hole into the parcel: no slit, no new features, area grows."""
    if cfg.is_geographic:
        return reject(feature_id, geom, "填洞必须在米制投影下执行", "fill_holes")
    if safe_geometry(geom) is None or geom.geom_type not in {"Polygon", "MultiPolygon"}:
        return reject(feature_id, geom, "不支持该几何", "fill_holes")
    parts = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    if not any(p.interiors for p in parts):
        return reject(feature_id, geom, "没有岛洞，无需填充", "none")
    filled_area = sum(Polygon(ring).area for poly in parts for ring in poly.interiors)
    rebuilt = [Polygon(poly.exterior) for poly in parts]
    candidate = rebuilt[0] if geom.geom_type == "Polygon" else MultiPolygon(rebuilt)
    if not candidate.is_valid:
        return reject(feature_id, geom, "填洞后几何不合法，已回退", "fill_holes")
    if cfg.repair.fill_all_max_area is not None and filled_area > cfg.repair.fill_all_max_area:
        return reject(feature_id, geom,
                      f"洞面积 {filled_area:.2f} 平方米超过填平上限 {cfg.repair.fill_all_max_area:.2f}", "fill_holes")
    res = result(feature_id, geom, candidate, "fill_holes", "auto_accepted",
                 "按所选方式填平岛洞：无裂缝、单要素，面积增加")
    res.diff.update(filled_area=filled_area, hole_count_before=sum(len(p.interiors) for p in parts), hole_count_after=0)
    return res
