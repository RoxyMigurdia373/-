"""Export an attributed copy without changing source geometries or fields."""
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import json
import re
import tempfile

import geopandas as gpd
from .io import write_gpkg_layer

LABELS = {
    "EMPTY_GEOMETRY": "空几何", "INVALID_COORDINATE": "非法坐标", "MULTIPART": "多部件",
    "HAS_HOLE": "岛洞", "INVALID_HOLE": "非法洞", "SELF_INTERSECTION": "自相交",
    "TOUCH_ISLAND": "触岛", "SHARP_ANGLE": "尖锐内角", "DUPLICATE_VERTEX": "重复顶点",
    "SHORT_SEGMENT": "短线段", "SMALL_AREA": "小面积", "DUPLICATE_GEOMETRY": "重复几何",
    "OVERLAP": "面间重叠", "SLIVER": "细长区域", "GAP": "覆盖空隙",
}


def annotate_layer(gdf, report, cfg):
    ids = gdf[cfg.id_field].astype(str).tolist() if cfg.id_field and cfg.id_field in gdf.columns else [str(x) for x in gdf.index]
    if len(set(ids)) != len(ids):
        raise ValueError("要素 ID 不唯一，无法准确写入问题字段；请改用唯一字段或留空后重新检查")
    groups = defaultdict(dict)
    known = set(ids)
    for issue in report.issues:
        for fid in {issue.feature_id, issue.related_feature_id}:
            if fid in known:
                groups[fid][issue.issue_id] = issue
    result = gdf.copy()
    fields = {}
    used = {str(name).lower() for name in result.columns}
    for name in ["topo_issue", "topo_types", "topo_count", "topo_level", "topo_state"]:
        candidate, n = name, 1
        while candidate.lower() in used:
            candidate = f"{name}_{n}"
            n += 1
        fields[name] = candidate
        used.add(candidate.lower())
    rows = []
    rank = {"INFO": 0, "WARN": 1, "ERROR": 2}
    incomplete = report.sampled or any(i.context.get("truncated") or "error" in i.context for i in report.issues)
    for fid in ids:
        issues = list(groups[fid].values())
        types = sorted({i.issue_type for i in issues})
        state = "报告范围内未发现问题" if not issues else "发现问题"
        if incomplete:
            state += "（检查不完整，需复核）"
        rows.append({"topo_issue": "；".join(LABELS[t] for t in types) if types else "未发现问题",
                     "topo_types": ";".join(types), "topo_count": len(issues),
                     "topo_level": max((i.severity for i in issues), key=rank.get) if issues else "NONE",
                     "topo_state": state})
    for name, actual in fields.items():
        result[actual] = [row[name] for row in rows]
    return result, fields


def write_vector_export(gdf, report, cfg, output_dir):
    out = Path(output_dir)
    layer, fields = annotate_layer(gdf, report, cfg)
    path = out / "checked_features.gpkg"
    write_gpkg_layer(layer, path, "checked_features")
    return fields


def write_report_bundle(output_dir):
    out = Path(output_dir)
    with ZipFile(out / "check_reports.zip", "w", compression=ZIP_DEFLATED) as archive:
        for name in ["report.json", "issues.geojson", "report.csv", "summary.txt"]:
            archive.write(out / name, name)


def write_shp_zip(source, layer_name, output_path):
    """Export one layer with deterministic DBF field names and explicit limits."""
    frame = gpd.read_file(source, layer=layer_name, engine="pyogrio")
    output_path = Path(output_path)
    with tempfile.TemporaryDirectory(prefix="shp-export-", dir=output_path.parent) as temporary:
        folder = Path(temporary)
        attributes = frame.drop(columns=frame.geometry.name)
        (folder / "attributes_full.json").write_text(
            attributes.to_json(orient="records", force_ascii=False, date_format="iso", double_precision=15, default_handler=str), encoding="utf-8")
        names, used, truncated = {}, set(), set()
        for index, column in enumerate(attributes.columns):
            name = re.sub(r"[^A-Za-z0-9_]", "_", str(column)).strip("_") or f"field{index + 1}"
            if name[0].isdigit():
                name = "f_" + name
            base = name[:10]
            name, sequence = base, 1
            while name.lower() in used:
                suffix = f"_{sequence}"
                name = base[:10-len(suffix)] + suffix
                sequence += 1
            used.add(name.lower())
            names[column] = name
            values = frame[column]
            if values.dtype.kind in {"O", "U", "S", "M"} or str(values.dtype).startswith("string"):
                def text_value(value):
                    if value is None:
                        return None
                    text = json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (list, dict, tuple)) else str(value)
                    data = text.encode("utf-8")
                    if len(data) > 254:
                        truncated.add(str(column))
                        return data[:254].decode("utf-8", errors="ignore")
                    return text
                frame[column] = values.map(text_value).where(values.notna(), None)
        # Keep the geometry column separate from renamed attributes.
        geometry = frame.geometry.copy()
        exported = gpd.GeoDataFrame(frame.drop(columns=frame.geometry.name).rename(columns=names))
        geometry_name = "shape"
        while geometry_name in exported.columns:
            geometry_name += "_"
        exported[geometry_name] = geometry
        exported = exported.set_geometry(geometry_name, crs=frame.crs)
        exported.to_file(folder / "parcels.shp", driver="ESRI Shapefile", engine="pyogrio", index=False, encoding="UTF-8")
        (folder / "fields.json").write_text(json.dumps({"source_layer": layer_name, "crs": str(frame.crs),
            "field_mapping": names, "truncated_text_fields": sorted(truncated)}, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / "README.txt").write_text(
            "SHP 导出（UTF-8）\n请保留 parcels.shp/.shx/.dbf/.prj/.cpg 配套文件。\n"
            "字段名受 SHP 的 10 字符限制，原字段名对照见 fields.json。\n"
            "DBF 长文本最多 254 字节，超长字段清单见 fields.json；完整属性按相同行顺序保存在 attributes_full.json。\n"
            "日期、复杂属性可能转换为文本；精确属性类型及完整多图层成果请使用 GPKG。\n"
            + ("原数据没有 CRS，因此不包含 .prj。\n" if frame.crs is None else ""), encoding="utf-8")
        packed = folder / "export.zip"
        with ZipFile(packed, "w", compression=ZIP_DEFLATED) as archive:
            for path in sorted(folder.iterdir()):
                if path != packed:
                    archive.write(path, path.name)
        packed.replace(output_path)
    return output_path
