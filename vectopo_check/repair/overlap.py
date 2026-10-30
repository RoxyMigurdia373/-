"""Resolve genuine polygon overlaps: the smaller claim yields to the larger one.

Real cadastral data rarely comes with a natural "correct" side, so the rule is
deterministic and area preserving: the largest claim keeps its geometry, and any
neighbour overlapping it either has the overlap trimmed away (when the remaining
parcel is still a usable parcel) or is absorbed into that neighbour.
"""

from shapely import STRtree

from .base import reject, result, validate_candidate
from ..checks.base import safe_geometry

CLIP = "clip_overlap"
MERGE = "merge_overlap"


def priority_order(frame):
    """Largest claim first; layer order breaks ties so a duplicate keeps the earlier row."""
    areas = frame.geometry.area
    positions = {fid: position for position, fid in enumerate(frame.index)}
    ordered = sorted(frame.index, key=lambda fid: (-float(areas.loc[fid]), positions[fid]))
    return ordered


def _priority(order, fid, area):
    return (float(area), -order.get(str(fid), 0))


def _yields_to(context, fid, area, other_id, other_area):
    """Whether the other parcel holds the stronger claim on the shared area.

    While a layer is being processed the workflow pins one rank per feature so a
    parcel clipped halfway through the pass keeps outranking the neighbours it has
    not met yet; otherwise the shrink would silently reorder the queue and leave
    chains touching (C2 clipped to 100 m² would let the 200 m² C3 keep its overlap
    with it).
    """
    ranks = getattr(context, "priority", None) or {}
    mine, theirs = ranks.get(str(fid)), ranks.get(str(other_id))
    if mine is not None and theirs is not None:
        return mine > theirs
    return _priority(context.order, fid, area) < _priority(context.order, other_id, other_area)


def repair(feature_id, geom, attrs, cfg):
    action = CLIP
    if safe_geometry(geom) is None or not geom.is_valid or geom.geom_type != "Polygon":
        return reject(feature_id, geom, "重叠面处理仅适用于有效单部件面", action)
    context = cfg._repair_context
    if context is None:
        return reject(feature_id, geom, "缺少邻接图层上下文 RepairContext", action)
    losses = []
    for fid, other, other_attrs in context.neighbors(geom, str(feature_id), include_blocked=True):
        if other.geom_type != "Polygon" or not other.is_valid:
            continue
        # Equal-area duplicates fall back to layer order: the later row yields.
        if not _yields_to(context, feature_id, geom.area, fid, other.area):
            continue
        shared = geom.intersection(other)
        area = 0.0 if shared is None or shared.is_empty else float(shared.area)
        if area > cfg.tolerance.overlap_area:
            losses.append((fid, other, other_attrs, area))
    if not losses:
        return reject(feature_id, geom, "没有需要让出的重叠区域", "none")
    losses.sort(key=lambda item: (-item[3], item[0]))
    overlap_area = sum(item[3] for item in losses)
    trimmed = geom
    for _, other, _, _ in losses:
        trimmed = trimmed.difference(other)
        if trimmed is None or trimmed.is_empty:
            break
    removed = geom.area - (0.0 if trimmed is None or trimmed.is_empty else trimmed.area)
    if (trimmed is not None and not trimmed.is_empty and trimmed.geom_type == "Polygon"
            and trimmed.area >= cfg.tolerance.min_area and removed > cfg.tolerance.overlap_area):
        validated = validate_candidate(feature_id, geom, trimmed, attrs, cfg, CLIP)
        if validated.status != "rejected":
            validated.reason = ("裁剪让出与更大图斑的重叠区域，保留共边邻接"
                                if validated.status == "auto_accepted"
                                else "裁剪后需要人工复核" + (f"；{validated.reason}" if validated.reason else ""))
            validated.diff.update(removed_area=removed, overlap_area=overlap_area,
                                  largest_overlap_with=losses[0][0],
                                  kept_ratio=trimmed.area / geom.area if geom.area else 0.0,
                                  source_consumed=False, repair_choice="clip")
            return validated
    # Trimming would leave a sub-threshold or fragmented parcel: absorb it instead
    # so the lost claim keeps its area and its attributes stay in staging.
    for target, other, other_attrs, area in losses:
        merged = other.union(geom)
        if merged is None or merged.is_empty or merged.geom_type != "Polygon" or merged.interiors:
            continue
        validated = validate_candidate(target, other, merged, other_attrs, cfg, MERGE)
        if validated.status == "rejected":
            continue
        status = validated.status
        field = cfg.repair.class_field
        same_class = bool(field and attrs.get(field) is not None and attrs.get(field) == other_attrs.get(field))
        if not (same_class or cfg.repair.merge_without_class):
            # Never silently fuse two parcels that belong to different classes.
            status = "pending_review"
        reason = "裁剪后剩余面积不足阈值，合并到重叠最多的图斑；目标属性保留，源属性保留在 staging"
        if status == "pending_review":
            reason += "；地理坐标或面积保护要求人工复核" if validated.status == "pending_review" else "；分类不一致需人工复核"
        output = result(feature_id, geom, merged, MERGE, status, reason, target=target)
        output.diff.update(target_before_area=other.area, target_after_area=merged.area,
                           combined_before_area=other.area + geom.area,
                           conservation_error=merged.area - (other.area + geom.area - other.intersection(geom).area),
                           overlap_area=overlap_area, consumed_area=geom.area,
                           largest_overlap_with=target, source_consumed=True, repair_choice="merge")
        return output
    return result(feature_id, geom, geom, CLIP, "pending_review",
                  "重叠区域内既无法裁剪出可用图斑，也无法合并为单部件面，需人工核实面积归属")


def find_overlaps(gdf, cfg, limit=None):
    """Remaining pairs whose shared area exceeds the reporting threshold."""
    features = [(str(fid), geom) for fid, geom in gdf.geometry.items()
                if safe_geometry(geom) is not None and geom.is_valid and geom.geom_type == "Polygon"]
    if len(features) < 2:
        return [], False
    tree = STRtree([geom for _, geom in features])
    found = []
    for position, (fid, geom) in enumerate(features):
        try:
            candidates = sorted(int(i) for i in tree.query(geom, predicate="intersects"))
        except Exception:
            candidates = sorted(int(i) for i in tree.query(geom))
        for other_position in candidates:
            if other_position <= position:
                continue
            other_id, other = features[other_position]
            area = geom.intersection(other).area
            if area > cfg.tolerance.overlap_area:
                found.append((fid, other_id, float(area)))
                if limit is not None and len(found) >= limit:
                    return found, True
    return found, False
