from .base import bridge_gap, contact_band, reject, result, shared_edge_length, validate_candidate
from ..checks.base import safe_geometry


def repair(feature_id, geom, attrs, cfg):
    action = "merge_small_area"
    if safe_geometry(geom) is None or not geom.is_valid or geom.geom_type != "Polygon":
        return reject(feature_id, geom, "碎面合并仅处理有效单部件面", action)
    if geom.area >= cfg.tolerance.min_area:
        return reject(feature_id, geom, "面积未低于阈值", action)
    context = cfg._repair_context
    if context is None:
        return reject(feature_id, geom, "缺少邻接图层上下文 RepairContext", action)
    targets = []
    overlap_epsilon = min(cfg.tolerance.distance ** 2, 1e-6) if cfg.simple_mode else 0.0
    tolerance = cfg.tolerance.distance
    has_neighbor = False
    for fid, other, other_attrs in context.neighbors(geom.buffer(tolerance) if tolerance > 0 else geom,
                                                     str(feature_id), include_blocked=True):
        # Real data carries sub-millimetre gaps and coordinate noise, so an exact
        # boundary intersection reports 0 for parcels that do share an edge.
        exact = geom.boundary.intersection(other.boundary).length
        edge = shared_edge_length(geom, other, tolerance)
        overlap = geom.intersection(other).area
        # A shared edge is a band within tolerance; a corner touch is only a dot.
        adjacent = contact_band(geom, other, tolerance) > tolerance ** 2
        has_neighbor |= exact > 0 or (adjacent and edge > tolerance) or overlap > overlap_epsilon
        if fid in context.blocked or other.geom_type != "Polygon" or overlap > overlap_epsilon:
            continue
        if edge > tolerance and adjacent:
            targets.append((fid, other, other_attrs, edge))
    if not targets:
        if cfg.repair.delete_isolated_small_area and not has_neighbor:
            # Invalid geometries are absent from the spatial index: never infer
            # isolation when a nearby invalid feature could be a neighbor.
            for fid in context.invalid_ids:
                other, _ = context.records[fid]
                if fid != str(feature_id) and other is not None and not other.is_valid and other.envelope.intersects(geom.envelope):
                    return reject(feature_id, geom, "附近存在非法几何，无法确认无共边，保留待核查", action)
            output = result(feature_id, geom, None, "delete_isolated_small_area",
                            "pending_review" if cfg.is_geographic else "auto_accepted",
                            "小于面积阈值且无共边邻面，删除；原始几何和属性保留在 staging")
            output.diff.update(deleted=True, removed_area=geom.area)
            return output
        return reject(feature_id, geom, "无可合并的共边邻接面；共点不算邻接", action)
    ordered = sorted(targets, key=lambda t: (-(t[3] if cfg.repair.small_area_strategy == "longest_shared_edge" else t[1].area), t[0]))
    fallback = None
    for target, other, other_attrs, edge in ordered:
        merged = other.union(geom)
        if merged.geom_type == "MultiPolygon":
            # A sub-millimetre seam between two separately digitised parcels must
            # not block the merge: bridge it inside the tolerance band.
            bridged = bridge_gap(merged, cfg.tolerance.distance)
            if bridged is not None:
                merged = bridged
        cut_loss = 0.0
        cut_status = "auto_accepted"
        if (cfg.simple_mode and cfg.repair.hole_action in {"cut_open", "slit"}
                and merged.geom_type == "Polygon" and merged.interiors):
            from .cut_open import repair as cut_open
            cut = cut_open(target, merged, other_attrs, cfg)
            if cut.status == "rejected":
                fallback = fallback or reject(feature_id, geom, cut.reason, action)
                continue
            cut_loss = merged.area - cut.repaired_geom.area
            merged = cut.repaired_geom
            cut_status = cut.status
        validated = validate_candidate(target, other, merged, other_attrs, cfg, action)
        if validated.status == "rejected":
            fallback = fallback or reject(feature_id, geom, validated.reason, action)
            continue
        field = cfg.repair.class_field
        same_class = bool(field and attrs.get(field) is not None and attrs.get(field) == other_attrs.get(field))
        status = "auto_accepted" if (same_class or cfg.repair.merge_without_class) and not cfg.is_geographic and cut_status == "auto_accepted" else "pending_review"
        output = result(feature_id, geom, merged, action, status,
                        "按共边策略合并，目标属性保留；源属性在 staging 保留" if status == "auto_accepted" else "属性归属、地理坐标或切口面积保护条件要求人工复核",
                        target=target)
        output.diff.update(target_before_area=other.area, target_after_area=merged.area,
                           combined_before_area=other.area + geom.area,
                           conservation_error=merged.area - other.area - geom.area,
                           shared_edge_length=edge, strategy=cfg.repair.small_area_strategy,
                           source_consumed=True, post_merge_cut_area=cut_loss,
                           tolerated_overlap_area=other.intersection(geom).area)
        if status == "auto_accepted" or not cfg.simple_mode:
            return output
        if fallback is None or fallback.status == "rejected":
            fallback = output
    return fallback or reject(feature_id, geom, "所有共边邻面的合并候选均未通过复核", action)
