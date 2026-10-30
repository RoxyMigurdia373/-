"""Pipeline tests: the one function both front ends call."""

from __future__ import annotations

import json

import geopandas as gpd
import pytest
from shapely.geometry import box

from vectopo_check.pipeline import Cancelled, run_pipeline


def make_dataset(tmp_path, geometries=None, crs="EPSG:32650", layer="parcels"):
    geometries = geometries or {"a": box(0, 0, 20, 20), "b": box(10, 0, 30, 20)}
    frame = gpd.GeoDataFrame({"name": list(geometries)},
                             geometry=list(geometries.values()), crs=crs)
    path = tmp_path / "parcels.gpkg"
    frame.to_file(path, layer=layer, driver="GPKG", engine="pyogrio")
    return path


def test_pipeline_writes_every_report_and_repairs(tmp_path):
    path = make_dataset(tmp_path)
    out = tmp_path / "out"

    payload = run_pipeline(path, layer="parcels", output_dir=out, repair=True)

    assert payload["report"].feature_count == 2
    assert payload["report"].summary.get("OVERLAP") == 1
    for name in ("report.json", "issues.geojson", "report.csv", "summary.txt", "check_reports.zip",
                 "checked_features.gpkg", "repair_report.json", "repair_staging.gpkg"):
        assert (out / name).is_file(), name
    assert payload["severity_counts"]["ERROR"] == 1
    assert payload["repair_error"] is None
    verification = payload["repair_verification"]
    assert verification["passed"] is True
    assert verification["overlap_pairs"] == 0
    assert payload["repair_summary"]["auto_accepted"] == 1
    processed = gpd.read_file(out / "repair_staging.gpkg", layer="processed", engine="pyogrio")
    assert sorted(round(area, 6) for area in processed.area) == [200.0, 400.0]
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["issue_count"] == payload["report"].issue_count


def test_pipeline_without_repair_leaves_no_repair_outputs(tmp_path):
    path = make_dataset(tmp_path)
    out = tmp_path / "check-only"

    payload = run_pipeline(path, layer="parcels", output_dir=out)

    assert payload["repair_summary"] is None
    assert payload["repair_verification"] is None
    assert payload["repair_enabled"] is False
    assert (out / "checked_features.gpkg").is_file()
    assert not (out / "repair_report.json").exists()
    assert not (out / "repair_staging.gpkg").exists()


def test_cancel_stops_before_anything_is_written(tmp_path):
    path = make_dataset(tmp_path)
    out = tmp_path / "cancelled"

    with pytest.raises(Cancelled):
        run_pipeline(path, layer="parcels", output_dir=out, repair=True, cancel=lambda: True)

    assert not (out / "report.json").exists()


def test_exports_can_be_skipped(tmp_path):
    path = make_dataset(tmp_path)
    out = tmp_path / "no-export"

    payload = run_pipeline(path, layer="parcels", output_dir=out, exports=False)

    assert payload["vector_fields"] is None
    assert not (out / "checked_features.gpkg").exists()
    assert (out / "report.json").is_file()


def test_repair_failure_keeps_the_check_report(tmp_path, monkeypatch):
    import vectopo_check.pipeline as pipeline_module

    def explode(*args, **kwargs):
        raise RuntimeError("模拟修复失败")

    monkeypatch.setattr(pipeline_module, "run_repair", explode)
    path = make_dataset(tmp_path)
    out = tmp_path / "repair-error"

    payload = run_pipeline(path, layer="parcels", output_dir=out, repair=True)

    assert "模拟修复失败" in payload["repair_error"]
    assert payload["repair_summary"] is None
    assert (out / "report.json").is_file()
    assert payload["report"].issue_count == 1


def test_layer_is_honoured_for_multilayer_datasets(tmp_path):
    path = make_dataset(tmp_path)
    frame = gpd.GeoDataFrame({"name": ["solo"]}, geometry=[box(0, 0, 1, 1)], crs="EPSG:32650")
    frame.to_file(path, layer="other", driver="GPKG", engine="pyogrio")

    payload = run_pipeline(path, layer="other", output_dir=tmp_path / "layer")
    assert payload["report"].feature_count == 1
    assert payload["report"].layer == "other"

    with pytest.raises(ValueError, match="图层"):
        run_pipeline(path, output_dir=tmp_path / "no-layer")


def test_progress_messages_reach_the_caller(tmp_path):
    path = make_dataset(tmp_path)
    seen = []
    run_pipeline(path, layer="parcels", output_dir=tmp_path / "progress", repair=True,
                 progress=seen.append)
    assert seen
    assert any("检查图斑" in message for message in seen)
    assert any("读取" in message for message in seen)
