import json
import importlib
from typing import get_args
import geopandas as gpd
import pytest
from pydantic import ValidationError
from shapely.geometry import Polygon, MultiPolygon, box
from vectopo_check.config import Config, load_config
from vectopo_check.models import IssueType
from vectopo_check.checks.base import make_issue
from vectopo_check.engine import check_layer, dedupe_and_limit, SINGLE_CHECKERS, PAIR_CHECKERS
from vectopo_check.report import write_reports
from . import fixtures as f


def test_registry():
    covered = {t for checker in SINGLE_CHECKERS + PAIR_CHECKERS for t in getattr(checker, "ISSUE_TYPES", (checker.ISSUE_TYPE,))}
    assert covered == set(get_args(IssueType))


@pytest.mark.parametrize("patch", [{"tolerance": {"distance": -1}}, {"rules": {"max_pairs": 0}}, {"rules": {"enable": ["NESTED"]}}, {"tolerance": {"min_area": float("inf")}}, {"unknown": 1}])
def test_bad_config(patch):
    with pytest.raises(ValidationError):
        Config.model_validate(patch)


def test_dedupe(cfg):
    items = [make_issue("a", "TOUCH_ISLAND", "ERROR", location=[1, 1]),
             make_issue("a", "INVALID_HOLE", "ERROR", location=[1, 1]),
             make_issue("a", "SMALL_AREA", "INFO", location=[2, 2]),
             make_issue("a", "SMALL_AREA", "WARN", location=[2, 2])]
    result = dedupe_and_limit(items, cfg)
    assert [(i.issue_type, i.severity) for i in result] == [("INVALID_HOLE", "ERROR"), ("SMALL_AREA", "WARN")]


def test_strict_json_nan(cfg, tmp_path):
    report = check_layer(gpd.GeoDataFrame(geometry=[f.nan_coord, None]), cfg)
    write_reports(report, tmp_path)
    def reject(value):
        raise AssertionError(value)
    for name in ["report.json", "issues.geojson"]:
        json.loads((tmp_path / name).read_text(encoding="utf-8"), parse_constant=reject)


def test_custom_ids_and_nondefault_index(cfg):
    cfg.input.id_field = "key"
    gdf = gpd.GeoDataFrame({"key": ["x", "y"]}, geometry=f.identical_pair, index=[30, 10])
    report = check_layer(gdf, cfg)
    duplicate = next(i for i in report.issues if i.issue_type == "DUPLICATE_GEOMETRY")
    assert (duplicate.feature_id, duplicate.related_feature_id) == ("x", "y")


def test_nonpolygon_rejected(cfg):
    from shapely.geometry import Point
    with pytest.raises(ValueError, match="面"):
        check_layer(gpd.GeoDataFrame(geometry=[Point(0, 0)]), cfg)


def test_near_hole_and_coordinate_range(cfg):
    from vectopo_check.checks.single import touch_island, invalid_coordinate
    poly = Polygon(f.square.exterior, [box(.0005, 2, 2, 4).exterior])
    issues = touch_island.check("a", poly, {}, cfg)
    assert issues[0].severity == "WARN" and issues[0].context["touch_type"] == "near_hole_exterior"
    cfg.is_geographic = True
    assert invalid_coordinate.check("a", box(180, 0, 181, 1), {}, cfg)[0].context["reason"] == "out_of_range"


def test_indexed_self_intersection_and_multipart(cfg):
    from vectopo_check.checks.single import self_intersection, multipart
    cfg.rules.segment_index_threshold = 1
    assert self_intersection.check("a", f.bowtie, {}, cfg)
    cfg.rules.multipart_direct_threshold = 1
    issues = multipart.check("a", MultiPolygon([f.square, f.square]), {}, cfg)
    assert issues[0].context["min_interpart_distance"] == 0


def test_gap_sliver_enable(cfg):
    geoms = [box(0, 0, 3, 10), box(3.1, 0, 6, 10), box(3, 0, 3.1, 1)]
    cfg.tolerance.min_area = .1
    cfg.rules.enable = ["SLIVER"]
    report = check_layer(gpd.GeoDataFrame(geometry=geoms), cfg)
    assert any(i.feature_id == "__gap__" and i.issue_type == "SLIVER" for i in report.issues)
