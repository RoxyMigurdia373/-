from collections import Counter
import json
from pathlib import Path

import geopandas as gpd

from . import holes, touch_island, small_area, overlap
from .base import RepairContext, reject, geometry_diff
from ..checks.base import json_finite, is_geographic, polygons
from ..config import Config
from ..io import write_gpkg_layer

# Overlap repair is order dependent: one accepted clip can unblock a pair that
# failed earlier in the same pass, so retry a bounded number of times.
MAX_OVERLAP_ROUNDS = 3
RESIDUAL_LIMIT = 2000


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
    stage_original, split_into, part_sources = original, {}, {}
    if cfg.simple_mode:
        from .multipart import split_layer
        if progress:
            progress("正在拆分多部件，保留各部件原始属性…")
        working, stage_original, split_into, part_sources, records, proposals = split_layer(original)
    context = RepairContext(working, working.index)
    # Whole-layer phases, so accepted edits are visible to subsequent neighbors.
    phases = [("INVALID_HOLE", holes), ("TOUCH_ISLAND", touch_island),
              ("OVERLAP", overlap), ("SMALL_AREA", small_area)]
    small_rounds = 0
    overlap_rounds = 0
    for issue_type, module in phases:
        accepted_this_round = 0
        if issue_type == "SMALL_AREA":
            small_rounds += 1
            ids = list(working.index[working.geometry.area < cfg.tolerance.min_area])
        elif issue_type == "OVERLAP":
            overlap_rounds += 1
            # Settle the largest claim first so each smaller claim absorbs its
            # whole share in one edit instead of being clipped repeatedly.
            ids = overlap.priority_order(working)
            # Pin the ranking for the whole pass: a parcel clipped down to a few
            # square metres must keep its claim over the neighbours it has not
            # met yet, or the queue silently reorders and chains stay overlapping.
            context.priority = {str(fid): rank for rank, fid in enumerate(ids)}
        else:
            ids = list(working.index)
        for position, fid in enumerate(ids):
            if progress and position % 100 == 0:
                progress(f"正在处理 {issue_type}：{position} / {len(ids)}，已记录 {len(records)} 笔处理"
                         + (f"，第 {small_rounds} 轮" if issue_type == "SMALL_AREA" else "")
                         + (f"，第 {overlap_rounds} 轮" if issue_type == "OVERLAP" else ""))
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
            if cfg.simple_mode:
                envelope = (geom.envelope.union(candidate_geom.envelope) if candidate_geom is not None else geom.envelope).envelope.buffer(cfg.tolerance.distance)
                affected = context.nearby(envelope) | {fid}
                if target:
                    affected.add(target)
                before_frame = working.loc[sorted(affected)]
                candidate = before_frame.copy()
            else:
                before_frame = working
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
                after_frame = candidate
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
                accepted_this_round += 1
                if cfg.simple_mode:
                    if target:
                        working.loc[target, geometry_name] = candidate_geom
                        working.drop(index=fid, inplace=True)
                    elif candidate_geom is None:
                        working.drop(index=fid, inplace=True)
                    else:
                        working.loc[fid, geometry_name] = candidate_geom
                else:
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
        # Every accepted small-area operation removes one feature. Thus each
        # additional productive pass strictly decreases the finite feature count.
        if cfg.simple_mode and issue_type == "SMALL_AREA" and accepted_this_round and (working.geometry.area < cfg.tolerance.min_area).any():
            phases.append(("SMALL_AREA", small_area))
        # A clip accepted in this pass can unblock a pair whose candidate failed
        # earlier (the third party had not yielded yet); retry, bounded so a
        # genuinely impossible pair cannot loop.
        if (cfg.simple_mode and issue_type == "OVERLAP" and accepted_this_round
                and overlap_rounds < MAX_OVERLAP_ROUNDS
                and any(r["issue_type"] == "OVERLAP" and r["status"] != "auto_accepted"
                        and r["feature_id"] not in blocked for r in records)):
            phases.append(("OVERLAP", overlap))
            phases.append(("SMALL_AREA", small_area))
    remaining = []
    last = {r["feature_id"]: r for r in records}
    for fid, geom in working.geometry.items():
        kinds = []
        if geom is not None and geom.geom_type in {"MultiPolygon", "GeometryCollection"}:
            kinds.append("MULTIPART")
        if geom is not None and geom.area < cfg.tolerance.min_area:
            kinds.append("SMALL_AREA")
        if kinds:
            remaining.append({"feature_id":fid, "source_id":part_sources.get(fid,fid), "issues":kinds,
                              "area_m2":geom.area, "reason":last.get(fid,{}).get("reason", "几何非法或关联候选待审核，无法安全自动处理")})
    # Overlap is a *relation*, not a property of one row: only a fresh scan of the
    # processed layer can prove the output is clean, so "passed" stays honest.
    residual, truncated = ([], False)
    if any(r["issue_type"] == "OVERLAP" for r in records) or blocked:
        residual, truncated = overlap.find_overlaps(working, cfg, limit=RESIDUAL_LIMIT)
    by_feature = {}
    for first, second, area in residual:
        # Attribute a residual pair to the side that actually tried and failed.
        owner = next((fid for fid in (first, second)
                      if last.get(fid, {}).get("issue_type") == "OVERLAP"), first)
        by_feature.setdefault(owner, []).append(
            {"related_feature_id": second if owner == first else first, "overlap_area": area})
    known = {item["feature_id"]: item for item in remaining}
    for fid, pairs in by_feature.items():
        item = known.get(fid)
        if item is None:
            geom = working.loc[fid, geometry_name]
            item = {"feature_id":fid, "source_id":part_sources.get(fid,fid), "issues":[],
                    "area_m2":float(geom.area) if geom is not None else None, "reason":""}
            remaining.append(item)
            known[fid] = item
        item["issues"].append("OVERLAP")
        item["overlap_pairs"] = pairs
        reason = last.get(fid, {}).get("reason") if last.get(fid, {}).get("issue_type") == "OVERLAP" else None
        item["reason"] = reason or item.get("reason") or "重叠面未自动处理，需人工核对面积归属"
    return {"original": original, "stage_original":stage_original, "accepted": working, "records": records, "proposals": proposals,
            "merged_into": merged_into, "split_into":split_into, "part_sources":part_sources,
            "verification":{"passed":not remaining, "small_area_rounds":small_rounds, "overlap_rounds":overlap_rounds,
                            "remaining_small_area":sum("SMALL_AREA" in r["issues"] for r in remaining),
                            "remaining_multipart":sum("MULTIPART" in r["issues"] for r in remaining),
                            "remaining_overlap":len(by_feature), "overlap_pairs":len(residual),
                            "overlap_truncated":truncated, "remaining":remaining}}


def write_repair_outputs(bundle, output_dir):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    records = bundle["records"]
    original, accepted = bundle["original"], bundle["accepted"]
    payload = {"tool": "vectopo-check", "crs": str(original.crs) if original.crs else "unknown",
               "summary": dict(Counter(r["status"] for r in records)), "repairs": records,
               "verification":bundle.get("verification"), "split_into":bundle.get("split_into", {}),
               "geometry_storage": {"geom_original": "WKT text", "geom_repaired": "native GPKG geometry"},
               "notes": ["staging 图层只包含自动接受的变化；pending_review/rejected 不应用",
                         "candidates 图层保存候选几何供审核，不是已批准成果",
                         "合并源行保留为 NULL 几何并标记 merged_into；目标行持有合并面",
                         "孤立小图斑删除行在 staging 标记 deleted，保留原始几何和属性；processed 不包含已删除行",
                         "拆分父行在 staging 标记 split，子行 source_id 指向原始父行；源属性继承至各子部件",
                         "verification 为处理后多部件/小面积/重叠面复核；无进展时停止并列出未解决图斑，不冒充全部合格"]}
    accepted_export = accepted.copy()
    id_fields = {}
    for base, values in [("topo_id",list(accepted.index)), ("topo_src",[bundle.get("part_sources",{}).get(fid,fid) for fid in accepted.index])]:
        name = base
        while name in accepted_export.columns:
            name += "_source"
        accepted_export[name] = values
        id_fields[base] = name
    payload["processed_id_fields"] = id_fields
    payload = json_finite(payload)
    (out / "repair_report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    stage_source = bundle.get("stage_original", original)
    stage = stage_source.copy()
    # Rename reserved export columns, preserving source attributes.
    reserved = {"geom_original", "geom_repaired", "repair_id", "repair_status", "merged_into", "source_id"}
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
    stage["geom_original"] = [g.wkt if g is not None else None for g in stage_source.geometry]
    stage["repair_id"] = list(stage_source.index)
    stage["merged_into"] = [bundle["merged_into"].get(fid) for fid in stage_source.index]
    touched = {fid for r in records if r["status"] == "auto_accepted" for fid in (r["feature_id"], r.get("target_feature_id"))}
    stage["source_id"] = [bundle.get("part_sources", {}).get(fid, fid) for fid in stage_source.index]
    stage["repair_status"] = ["split" if fid in bundle.get("split_into", {}) else
                              ("merged" if fid in bundle["merged_into"] else "deleted") if fid not in accepted.index else
                              "auto_accepted" if fid in touched or fid in bundle.get("part_sources", {}) else "unchanged"
                              for fid in stage_source.index]
    stage["geom_repaired"] = [accepted.loc[fid, accepted.geometry.name] if fid in accepted.index else None for fid in stage_source.index]
    path = out / "repair_staging.gpkg"
    # Replacing a dedicated output is allowed, never the source dataset.
    if path.exists():
        path.unlink()
    write_gpkg_layer(stage, path, "staging", geometry_name="geom_repaired")
    write_gpkg_layer(accepted_export, path, "processed")
    if bundle["proposals"]:
        proposals = gpd.GeoDataFrame(bundle["proposals"], geometry="geom_repaired", crs=original.crs)
        write_gpkg_layer(proposals, path, "candidates", geometry_name="geom_repaired")
    return payload
