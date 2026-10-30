import csv
import json
from pathlib import Path


def summary_text(report):
    lines = [f"{report.tool} {report.version}", f"输入: {report.input_path}",
             f"图层: {report.layer or '-'}", f"CRS: {report.crs}",
             f"要素数: {report.feature_count}", f"问题数: {report.issue_count}"]
    lines.extend(f"{key}: {value}" for key, value in report.summary.items())
    if report.is_geographic:
        lines.append("地理坐标系：距离单位为度、面积单位为平方度，建议投影后复核。")
    if report.crs == "unknown":
        lines.append("CRS 未知：量纲及坐标范围采用投影坐标默认值，需人工复核。")
    if report.sampled:
        lines.append(f"候选对已抽样：检查 {report.checked_pair_count} / {report.candidate_count} 对；结果不完整。")
    lines.append("GAP 使用凸包近似，边缘凹陷和有意保留的洞需人工复核。")
    return "\n".join(lines) + "\n"


def write_reports(report, output_dir):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    def dump(name, data):
        (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    dump("report.json", report.model_dump(mode="json"))
    features = []
    for issue in report.issues:
        geometry = issue.geometry or ({"type": "Point", "coordinates": issue.location} if issue.location else None)
        if geometry is None:
            continue
        props = issue.model_dump(exclude={"geometry", "location", "context"})
        props["context"] = json.dumps(issue.context, ensure_ascii=False, sort_keys=True, allow_nan=False)
        features.append({"type": "Feature", "geometry": geometry, "properties": props})
    # Preserve native coordinates and explicitly declare CRS (legacy GeoJSON CRS).
    collection = {"type": "FeatureCollection", "features": features}
    if report.crs != "unknown":
        collection["crs"] = {"type": "name", "properties": {"name": report.crs}}
    dump("issues.geojson", collection)
    columns = ["feature_id", "issue_type", "severity", "message", "x", "y", "suggested_fix", "related_feature_id"]
    with (out / "report.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for issue in report.issues:
            row = issue.model_dump(include=set(columns) - {"x", "y"})
            row.update(x=issue.location[0] if issue.location else "", y=issue.location[1] if issue.location else "")
            writer.writerow(row)
    (out / "summary.txt").write_text(summary_text(report), encoding="utf-8")
