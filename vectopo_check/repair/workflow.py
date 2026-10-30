from collections import Counter
import json
from pathlib import Path

import geopandas as gpd

from . import holes, touch_island, small_area
from .base import RepairContext, reject, geometry_diff
from ..checks.base import json_finite, is_geographic, polygons
from ..config import Config
from ..io import write_gpkg_layer


def audit(gdf, cfg):
    """No rule filtering, deduplication or per-feature truncation in repair validation."""
    from ..engine import SINGLE_CHECKERS, PAIR_CHECKERS, generate_candidates
    cfg._active_rules = set(cfg.rules.enable) if cfg.simple_mode else None
    ids = [str(i) for i in gdf.index]
    frame = gdf.copy()
    frame.attrs["vectopo_ids"] = ids
    findings = []
    for fid, (_, row) in zip(ids, frame.iterrows()):
        for checker in SINGLE_CHECKERS:
            if cfg.simple_mode and checker.ISSUE_TYPE not in cfg.rules.enable:
                continue
            try:
                findings.extend(checker.check(fid, row[frame.geometry.name], row.drop(frame.geometry.name).to_dict(), cfg))
            except Exception as exc:
                return None, f"复检异常：{exc}"
    pairs, sampled, _ = generate_candidates(frame, cfg)
    if sampled:
        return None, "复检候选对超出 max_pairs，无法确认完整结果"
    for checker in PAIR_CHECKERS:
        if cfg.simple_mode and checker.ISSUE_TYPE not in cfg.rules.enable:
            continue
        try:
            findings.extend(checker.check(frame, pairs, cfg))
        except Exception as exc:
            return None, f"复检异常：{exc}"
    # Preserve location identity for vertex/ring defects; equal counts at a new
    # location must not conceal an introduced sharp angle or intersection.
    local_types = {"SELF_INTERSECTION", "TOUCH_ISLAND", "SHARP_ANGLE", "DUPLICATE_VERTEX", "SHORT_SEGMENT"}
    counts = Counter((i.feature_id, i.issue_type, i.severity, i.related_feature_id,
                      tuple(round(v, cfg.rules.location_precision) for v in i.location)
                      if i.issue_type in local_types and i.location is not None else ()) for i in findings)
    overlap = {}
    for i in findings:
        if i.issue_type == "OVERLAP":
            overlap[(i.feature_id, i.related_feature_id)] = i.context["overlap_area"]
    return (counts, overlap), None


def regression(before, after):
    counts_before, overlap_before = before
    counts_after, overlap_after = after
    new = counts_after - counts_before
    # A pre-existing overlap must not grow even if issue counts stay constant.
    increased = [pair for pair, area in overlap_after.items() if area > overlap_before.get(pair, 0)]
    return bool(new or increased), {"new_findings": [list(k) + [v] for k, v in sorted(new.items(), key=lambda item: str(item[0]))],
                                    "increased_overlaps": increased}


def repair_layer(gdf, cfg=None, progress=None):
    cfg = (cfg or Config()).model_copy(deep=True)
    cfg.is_geographic = is_geographic(gdf.crs)
    ids = gdf[cfg.id_field].astype(str).tolist() if cfg.id_field and cfg.id_field in gdf.columns else [str(i) for i in gdf.index]
    if len(set(ids)) != len(ids) or any(fid.startswith("__") for fid in ids):
        raise ValueError("修复要求唯一且不以 __ 开头的要素 ID，请选择唯一字段或留空")
    original = gdf.copy()
    original.index = ids
    working = original.copy()
    records, proposals = [], []
    blocked = set()
    merged_into = {}
    geometry_name = working.geometry.name
    context = RepairContext(working, working.index)
    # Whole-layer phases, so accepted edits are visible to subsequent neighbors.
    for issue_type, module in [("INVALID_HOLE", holes), ("TOUCH_ISLAND", touch_island), ("SMALL_AREA", small_area)]:
        for position, fid in enumerate(ids):
            if progress and position % 100 == 0:
                progress(f"正在处理 {issue_type}：{position} / {len(ids)}，已记录 {len(records)} 笔处理")
            if fid not in working.index or fid in blocked:
                continue
            geom = working.loc[fid, geometry_name]
            if geom is None or geom.is_empty:
                continue
            if issue_type in {"INVALID_HOLE", "TOUCH_ISLAND"} and not any(len(p.interiors) for p in polygons(geom)):
                continue
            if issue_type == "SMALL_AREA" and geom.area >= cfg.tolerance.min_area:
                continue
            context.blocked = blocked
            cfg._repair_context = context
            attrs = working.loc[fid].drop(geometry_name).to_dict()
            try:
                proposal = module.repair(fid, geom, attrs, cfg)
            except Exception as exc:
                proposal = reject(fid, geom, f"修复异常，已回退：{exc}", module.__name__.split(".")[-1])
            if proposal.action == "none" or (proposal.status == "rejected" and proposal.reason.startswith("没有实际接触洞")):
                continue
            candidate_geom = proposal.repaired_geom
            target = proposal.target_feature_id
            before_geom = working.loc[target, geometry_name] if target else geom
            candidate = working.copy()
            validation = {}
            if proposal.status != "rejected":
                if target:
                    candidate.loc[target, geometry_name] = candidate_geom
                    candidate = candidate.drop(index=fid)
                elif candidate_geom is None:
                    candidate = candidate.drop(index=fid)
                else:
                    candidate.loc[fid, geometry_name] = candidate_geom
                if cfg.simple_mode:
                    # These five rules are feature-local or pair-local. Recheck
                    # changed faces and all neighboring envelopes, including
                    # blocked/pending neighbors, without global GAP/union work.
                    envelope = (geom.envelope.union(candidate_geom.envelope) if candidate_geom is not None else geom.envelope).envelope.buffer(cfg.tolerance.distance)
                    nearby = working.sindex.query(envelope)
                    affected = set(working.index[nearby]) | {fid}
                    if target:
                        affected.add(target)
                    before_frame = working.loc[working.index.isin(affected)]
                    after_frame = candidate.loc[candidate.index.isin(affected)]
                else:
                    before_frame, after_frame = working, candidate
                baseline, before_error = audit(before_frame, cfg)
                checked, after_error = audit(after_frame, cfg)
                if before_error or after_error:
                    proposal.status = "pending_review"
                    proposal.reason += "; " + (before_error or after_error)
                else:
                    has_new, validation = regression(baseline, checked)
                    if has_new:
                        proposal.status = "rejected"
                        proposal.reason = "全图层复检发现新增问题或重叠增长，已原子回退"
                        proposal.repaired_geom = geom
                if gdf.crs is None and proposal.status == "auto_accepted":
                    proposal.status = "pending_review"
                    proposal.reason += "；CRS 未知，强制人工复核"
            event_id = str(len(records) + 1)
            record = proposal.model_dump(exclude={"repaired_geom"})
            record.update(event_id=event_id, issue_type=issue_type,
                          validation=validation, candidate_diff=geometry_diff(before_geom, candidate_geom))
            if proposal.status == "rejected":
                record["diff"] = geometry_diff(geom, geom)
            records.append(json_finite(record))
            proposals.append({"event_id": event_id, "feature_id": fid, "target_id": target,
                              "action": proposal.action, "status": proposal.status, "reason": proposal.reason,
                              "geom_original": before_geom.wkt if before_geom is not None else None,
                              "source_original": geom.wkt, "geom_repaired": candidate_geom})
            if proposal.status == "auto_accepted":
                working = candidate
                updated_id = target or fid
                if candidate_geom is None:
                    context.remove(fid)
                else:
                    context.update(updated_id, working.loc[updated_id, geometry_name], working.loc[updated_id].drop(geometry_name).to_dict())
                if target:
                    merged_into[fid] = target
                    context.remove(fid)
            elif proposal.status == "pending_review":
                # Later phases must not accidentally accept an alternative to a pending edit.
                blocked.add(fid)
                if target:
                    blocked.add(target)
    return {"original": original, "accepted": working, "records": records, "proposals": proposals,
            "merged_into": merged_into}


def write_repair_outputs(bundle, output_dir):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    records = bundle["records"]
    original, accepted = bundle["original"], bundle["accepted"]
    payload = {"tool": "vectopo-check", "crs": str(original.crs) if original.crs else "unknown",
               "summary": dict(Counter(r["status"] for r in records)), "repairs": records,
               "geometry_storage": {"geom_original": "WKT text", "geom_repaired": "native GPKG geometry"},
               "notes": ["staging 图层只包含自动接受的变化；pending_review/rejected 不应用",
                         "candidates 图层保存候选几何供审核，不是已批准成果",
                         "合并源行保留为 NULL 几何并标记 merged_into；目标行持有合并面",
                         "孤立小图斑删除行在 staging 标记 deleted，保留原始几何和属性；processed 不包含已删除行"]}
    (out / "repair_report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    stage = original.copy()
    # Rename reserved export columns, preserving source attributes.
    reserved = {"geom_original", "geom_repaired", "repair_id", "repair_status", "merged_into"}
    used = set(stage.columns) | reserved
    rename = {}
    for col in stage.columns:
        if col in reserved and col != stage.geometry.name:
            name = col + "_source"
            while name in used:
                name += "_source"
            rename[col] = name
            used.add(name)
    stage = stage.rename(columns=rename)
    old_geometry = stage.geometry.name
    if old_geometry != "geom_repaired":
        stage = stage.rename_geometry("geom_repaired")
    stage["geom_original"] = [g.wkt if g is not None else None for g in original.geometry]
    stage["repair_id"] = list(original.index)
    stage["merged_into"] = [bundle["merged_into"].get(fid) for fid in original.index]
    stage["repair_status"] = [("merged" if fid in bundle["merged_into"] else "deleted") if fid not in accepted.index else "auto_accepted" if any(r["status"] == "auto_accepted" and fid in {r["feature_id"], r["target_feature_id"]} for r in records) else "unchanged" for fid in original.index]
    stage["geom_repaired"] = [accepted.loc[fid, accepted.geometry.name] if fid in accepted.index else None for fid in original.index]
    path = out / "repair_staging.gpkg"
    # Replacing a dedicated output is allowed, never the source dataset.
    if path.exists():
        path.unlink()
    write_gpkg_layer(stage, path, "staging", geometry_name="geom_repaired")
    write_gpkg_layer(accepted, path, "processed")
    if bundle["proposals"]:
        proposals = gpd.GeoDataFrame(bundle["proposals"], geometry="geom_repaired", crs=original.crs)
        write_gpkg_layer(proposals, path, "candidates", geometry_name="geom_repaired")
    return payload
