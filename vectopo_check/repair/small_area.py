from .base import reject, result, validate_candidate
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
    has_neighbor = False
    for fid, other, other_attrs in context.neighbors(geom, str(feature_id), include_blocked=True):
        edge = geom.boundary.intersection(other.boundary).length
        overlap = geom.intersection(other).area
        has_neighbor |= edge > 0 or overlap > 0
        if fid in context.blocked or other.geom_type != "Polygon" or overlap > 0:
            continue
        if edge > cfg.tolerance.distance:
            targets.append((fid, other, other_attrs, edge))
    if not targets:
        if cfg.repair.delete_isolated_small_area and not has_neighbor:
            # Invalid geometries are absent from the spatial index: never infer
            # isolation when a nearby invalid feature could be a neighbor.
            for fid, (other, _) in context.records.items():
                if fid != str(feature_id) and other is not None and not other.is_valid and other.envelope.intersects(geom.envelope):
                    return reject(feature_id, geom, "附近存在非法几何，无法确认无共边，保留待核查", action)
            output = result(feature_id, geom, None, "delete_isolated_small_area",
                            "pending_review" if cfg.is_geographic else "auto_accepted",
                            "小于面积阈值且无共边邻面，删除；原始几何和属性保留在 staging")
            output.diff.update(deleted=True, removed_area=geom.area)
            return output
        return reject(feature_id, geom, "无可合并的共边邻接面；共点不算邻接", action)
    target, other, other_attrs, edge = min(targets, key=lambda t: (-(t[3] if cfg.repair.small_area_strategy == "longest_shared_edge" else t[1].area), t[0]))
    merged = other.union(geom)
    validated = validate_candidate(target, other, merged, other_attrs, cfg, action)
    if validated.status == "rejected":
        return reject(feature_id, geom, validated.reason, action)
    field = cfg.repair.class_field
    same_class = bool(field and attrs.get(field) is not None and attrs.get(field) == other_attrs.get(field))
    status = "auto_accepted" if (same_class or cfg.repair.merge_without_class) and not cfg.is_geographic else "pending_review"
    output = result(feature_id, geom, merged, action, status,
                    "按共边策略合并，目标属性保留；源属性在 staging 保留" if status == "auto_accepted" else "未确认同类属性或使用地理坐标，需复核属性归属",
                    target=target)
    output.diff.update(target_before_area=other.area, target_after_area=merged.area,
                       combined_before_area=other.area + geom.area,
                       conservation_error=merged.area - other.area - geom.area,
                       shared_edge_length=edge, strategy=cfg.repair.small_area_strategy,
                       source_consumed=True)
    return output
