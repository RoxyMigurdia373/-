import json
import geopandas as gpd
from typer.testing import CliRunner
from vectopo_check.engine import run_check, check_layer
from vectopo_check.cli import app
from vectopo_check.report import write_reports
from . import fixtures as f


def test_end_to_end(sample_data, tmp_path, cfg):
    a = run_check(sample_data, "sample", cfg)
    b = run_check(sample_data, "sample", cfg)
    assert a.model_dump() == b.model_dump()
    assert sum(a.summary.values()) == a.issue_count == len(a.issues)
    assert a.summary["DUPLICATE_GEOMETRY"] == 1
    write_reports(a, tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == {"report.json", "report.csv", "issues.geojson", "summary.txt"}
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["issue_count"] == a.issue_count
    result = CliRunner().invoke(app, ["check", str(sample_data), "--layer", "sample", "--out", str(tmp_path), "--fail-on", "error"])
    assert result.exit_code == 1, result.output


def test_bad_geometries_and_geographic(cfg):
    gdf = gpd.GeoDataFrame(geometry=[None, f.empty, f.nan_coord, f.tiny_area, f.sharp_triangle], crs="EPSG:4326")
    cfg.rules.sharp_angle_degrees = 30
    report = check_layer(gdf, cfg)
    assert report.is_geographic
    assert report.summary["INVALID_COORDINATE"] == 1
    assert report.summary["EMPTY_GEOMETRY"] == 2
    assert all(i.severity == "WARN" for i in report.issues if i.issue_type in {"SMALL_AREA", "SHARP_ANGLE", "SLIVER"})


def test_enable_and_limit(cfg):
    cfg.rules.enable = ["INVALID_HOLE"]
    gdf = gpd.GeoDataFrame(geometry=[f.poly_with_hole])
    assert check_layer(gdf, cfg).issues == []
    cfg.rules.enable = ["DUPLICATE_GEOMETRY", "OVERLAP"]
    cfg.rules.max_pairs = 1
    cfg.rules.max_issues_per_feature = 1
    report = check_layer(gpd.GeoDataFrame(geometry=[f.square] * 4), cfg)
    assert report.sampled and report.checked_pair_count == 1
    assert len(report.issues) == 1 and report.issues[0].context["truncated"]


def test_exception_isolation(monkeypatch, cfg):
    from vectopo_check.checks.single import multipart
    def fail(*args):
        raise RuntimeError("intentional test failure")
    monkeypatch.setattr(multipart, "check", fail)
    report = check_layer(gpd.GeoDataFrame(geometry=[f.tiny_area, f.square]), cfg)
    assert report.summary["MULTIPART"] == 2
    assert report.summary["SMALL_AREA"] == 1
    assert any("error" in i.context for i in report.issues)
