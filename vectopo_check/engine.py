from collections import Counter, defaultdict
import json
from random import Random
from shapely import STRtree
from . import __version__
from .config import Config
from .models import Report
from .io import read_layer, validate_layer
from .checks.base import make_issue, safe_geometry, is_geographic
from .checks.single import (empty_geometry, invalid_coordinate, multipart, holes,
    self_intersection, touch_island, sharp_angle, duplicate_vertex, short_segment, small_area)
from .checks.pair import duplicate_geometry, overlap, sliver, gap

SINGLE_CHECKERS = [empty_geometry, invalid_coordinate, multipart, duplicate_vertex,
                  short_segment, small_area, holes, self_intersection, touch_island, sharp_angle]
PAIR_CHECKERS = [duplicate_geometry, overlap, sliver, gap]


def generate_candidates(gdf, cfg):
    finite = gdf.attrs.get("vectopo_finite")
    valid = [(i, geom) for i, geom in enumerate(gdf.geometry) if (finite[i] if finite is not None else safe_geometry(geom) is not None)]
    tree = STRtree([g for _, g in valid])
    # One-pass bounded reservoir: query each feature only once, with stable sampling.
    def pairs():
        for local_i, (i, geom) in enumerate(valid):
            try:
                neighbors = tree.query(geom, predicate="intersects")
            except Exception:
                # Keep potentially relevant pairs for per-checker isolation when
                # an invalid polygon cannot evaluate the exact predicate.
                neighbors = tree.query(geom)
            for local_j in sorted(neighbors):
                j = valid[int(local_j)][0]
                if i < j:
                    yield i, j
    limit = cfg.rules.max_pairs
    selected, count, rng = [], 0, Random(0)
    for pair in pairs():
        count += 1
        if count <= limit:
            selected.append(pair)
        else:
            slot = rng.randrange(count)
            if slot < limit:
                selected[slot] = pair
    return sorted(selected), count > limit, count


def dedupe_and_limit(issues, cfg):
    rank = {"INFO": 0, "WARN": 1, "ERROR": 2}
    def loc(issue):
        return tuple(round(v, cfg.rules.location_precision) for v in issue.location) if issue.location is not None else ()
    unique = {}
    for issue in issues:
        key = (issue.feature_id, issue.issue_type, loc(issue))
        old = unique.get(key)
        if old is None or rank[issue.severity] > rank[old.severity]:
            unique[key] = issue
    invalid_locs = {(i.feature_id, loc(i)) for i in unique.values() if i.issue_type == "INVALID_HOLE"}
    result = [i for i in unique.values() if not (i.issue_type == "TOUCH_ISLAND" and (i.feature_id, loc(i)) in invalid_locs)]
    result.sort(key=lambda i: (i.feature_id, i.issue_type, loc(i), i.related_feature_id or "", json.dumps(i.context, sort_keys=True)))
    groups = defaultdict(list)
    for issue in result:
        groups[issue.feature_id].append(issue)
    final = []
    for fid in sorted(groups):
        group = groups[fid]
        kept = group[:cfg.rules.max_issues_per_feature]
        if len(group) > len(kept):
            for issue in kept:
                issue.context = {**issue.context, "truncated": True, "issues_before_limit": len(group)}
        counts = Counter()
        for issue in kept:
            counts[issue.issue_type] += 1
            issue.issue_id = f"{fid}:{issue.issue_type}:{counts[issue.issue_type]}"
        final.extend(kept)
    return final


def build_report(issues, gdf, cfg, input_path, layer, sampled=False, candidate_count=0, checked_pair_count=0):
    summary = dict(sorted(Counter(i.issue_type for i in issues).items()))
    assert sum(summary.values()) == len(issues)
    return Report(version=__version__, input_path=str(input_path), layer=layer,
                  crs=str(gdf.crs) if gdf.crs else "unknown", is_geographic=cfg.is_geographic,
                  feature_count=len(gdf), issue_count=len(issues), summary=summary,
                  issues=issues, sampled=sampled, candidate_count=candidate_count, checked_pair_count=checked_pair_count)


def check_layer(gdf, cfg=None, input_path="<memory>", layer=None, progress=None):
    cfg = (cfg or Config()).model_copy(deep=True)
    validate_layer(gdf)
    cfg.is_geographic = is_geographic(gdf.crs)
    gdf = gdf.copy()
    gdf.attrs.pop("vectopo_finite", None)
    gdf.attrs.pop("vectopo_pair_safe", None)
    ids = gdf[cfg.id_field].astype(str).tolist() if cfg.id_field and cfg.id_field in gdf.columns else [str(x) for x in gdf.index]
    gdf.attrs["vectopo_ids"] = ids
    # Store one geometry-array reference outside pandas attrs: repeated geometry
    # Series access in pandas 3 copies attrs (including every feature ID).
    object.__setattr__(gdf, "_vectopo_geometries", gdf.geometry.array)
    enabled = set(cfg.rules.enable)
    cfg._active_rules = enabled
    active_single = [c for c in SINGLE_CHECKERS if enabled.intersection(getattr(c, "ISSUE_TYPES", (c.ISSUE_TYPE,)))]
    issues = []
    attr_rows = gdf.drop(columns=gdf.geometry.name).to_dict("records")
    for pos, geom in enumerate(gdf.geometry):
        if progress and pos % 1000 == 0:
            progress(f"正在检查图斑：{pos} / {len(gdf)}")
        fid, attrs = ids[pos], attr_rows[pos] if attr_rows else {}
        for checker in active_single:
            try:
                issues.extend(i for i in checker.check(fid, geom, attrs, cfg) if i.issue_type in enabled)
            except Exception as exc:
                kind = next(t for t in getattr(checker, "ISSUE_TYPES", (checker.ISSUE_TYPE,)) if t in enabled)
                issues.append(make_issue(fid, kind, message=f"{checker.__name__}: {exc}", context={"error": str(exc)}))
    needs_pairs = enabled.intersection({"DUPLICATE_GEOMETRY", "OVERLAP", "SLIVER"})
    if progress:
        progress("图斑检查完成，正在建立空间索引和筛选邻接面…")
    if needs_pairs:
        finite = [safe_geometry(g) is not None for g in gdf.geometry]
        gdf.attrs["vectopo_finite"] = finite
        gdf.attrs["vectopo_pair_safe"] = [ok and g.is_valid for ok, g in zip(finite, gdf.geometry)]
    candidates, sampled, total = generate_candidates(gdf, cfg) if needs_pairs else ([], False, 0)
    for checker in PAIR_CHECKERS:
        if not enabled.intersection(getattr(checker, "ISSUE_TYPES", (checker.ISSUE_TYPE,))):
            continue
        # Per-pair isolation: one failed intersection cannot discard other pairs.
        batches = [None] if checker is gap else candidates
        for pair_pos, pair in enumerate(batches):
            if progress and pair_pos % 1000 == 0:
                progress(f"正在检查 {checker.ISSUE_TYPE}：{pair_pos} / {len(batches)} 对")
            try:
                issues.extend(i for i in checker.check(gdf, [] if pair is None else [pair], cfg) if i.issue_type in enabled)
            except Exception as exc:
                fid = "__gap__" if pair is None else ids[pair[0]]
                kind = next(t for t in getattr(checker, "ISSUE_TYPES", (checker.ISSUE_TYPE,)) if t in enabled)
                issues.append(make_issue(fid, kind, message=f"{checker.__name__}: {exc}", context={"error": str(exc)}, related_feature_id=None if pair is None else ids[pair[1]]))
    return build_report(dedupe_and_limit(issues, cfg), gdf, cfg, input_path, layer, sampled, total, len(candidates))


def run_check(input_path, layer=None, cfg=None, repair=False, output_dir=None):
    gdf = read_layer(input_path, layer)
    if cfg is not None and cfg.simple_mode:
        from .preset import prepare_parcels
        gdf, cfg, _ = prepare_parcels(gdf, cfg)
    report = check_layer(gdf, cfg, input_path, layer)
    if repair:
        run_repair(gdf, cfg, output_dir or (cfg or Config()).output_dir, input_path)
    return report


def run_repair(gdf, cfg=None, output_dir="report", input_path=None, progress=None):
    from pathlib import Path
    from .repair.workflow import repair_layer, write_repair_outputs
    if input_path is not None and Path(input_path).resolve() in {
        (Path(output_dir) / name).resolve() for name in ("repair_report.json", "repair_staging.gpkg")
    }:
        raise ValueError("修复输出不能覆盖输入文件")
    bundle = repair_layer(gdf, cfg, progress=progress)
    if progress:
        progress("处理复核完成，正在写入 processed / staging 成果图层…")
    return write_repair_outputs(bundle, output_dir)
