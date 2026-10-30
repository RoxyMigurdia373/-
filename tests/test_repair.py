import json
import geopandas as gpd
import pytest
from shapely.geometry import Polygon, box
from typer.testing import CliRunner

from vectopo_check.config import Config
from vectopo_check.cli import app
from vectopo_check.repair import RepairContext
from vectopo_check.repair import small_area, holes, touch_island
from vectopo_check.repair.workflow import repair_layer, write_repair_outputs


def layer(geoms, **attrs):
    return gpd.GeoDataFrame(attrs, geometry=geoms, crs="EPSG:3857")


@pytest.mark.parametrize("strategy,target,area", [("longest_shared_edge", "1", 4.5), ("largest_area", "2", 10.5)])
def test_merge_strategies(strategy, target, area):
    # Small 0.5-area face: left shared edge=1, bottom shared edge=.5.
    geoms = [box(0, 0, .5, 1), box(-4, 0, 0, 1), box(0, -20, .5, 0)]
    gdf = layer(geoms, landclass=["a"]*3)
    cfg = Config()
    cfg.repair.small_area_strategy = strategy
    cfg.repair.class_field = "landclass"
    cfg._repair_context = RepairContext(gdf, ["0", "1", "2"])
    result = small_area.repair("0", geoms[0], {"landclass": "a"}, cfg)
    assert result.status == "auto_accepted", result.reason
    assert result.target_feature_id == target
    assert result.repaired_geom.area == area
    assert result.diff["conservation_error"] == 0


def touching():
    return Polygon(box(0, 0, 10, 10).exterior, [[(0, 5), (2, 4), (2, 6), (0, 5)]])


def test_touch_removal_protected():
    cfg = Config()
    result = touch_island.repair("0", touching(), {}, cfg)
    assert result.action == "remove_touching_hole"
    assert result.status == "pending_review"
    assert result.repaired_geom.is_valid
    assert len(result.repaired_geom.interiors) == 0
    assert result.diff["area_change"] == 2
    cfg.repair.touch_max_area_change = 3
    cfg.repair.touch_max_area_ratio = .1
    assert touch_island.repair("0", touching(), {}, cfg).status == "auto_accepted"


def test_outside_hole_rebuild_no_multipart():
    geom = Polygon(box(0, 0, 10, 10).exterior, [box(9, 2, 11, 4).exterior])
    result = holes.repair("0", geom, {}, Config())
    assert result.status == "pending_review", result.reason
    assert result.repaired_geom.is_valid
    assert result.repaired_geom.geom_type == "Polygon"
    assert result.repaired_geom.area == 98
    splitting = Polygon(box(0, 0, 10, 10).exterior, [box(4, -1, 6, 11).exterior])
    rejected = holes.repair("0", splitting, {}, Config())
    assert rejected.status == "rejected"
    assert rejected.repaired_geom.equals_exact(splitting, 0)


def test_fill_disabled_and_max_area():
    geom = Polygon(box(0, 0, 10, 10).exterior, [box(2, 2, 3, 3).exterior])
    cfg = Config()
    assert holes.repair("0", geom, {}, cfg).status == "rejected"
    cfg.repair.auto_fill_max_area = 1
    assert holes.repair("0", geom, {}, cfg).status == "auto_accepted"
    cfg.repair.fill_if_same_class = True
    assert holes.repair("0", geom, {}, cfg).status == "rejected"


def test_same_class_gate():
    hole = box(2, 2, 3, 3)
    geom = Polygon(box(0, 0, 10, 10).exterior, [hole.exterior])
    gdf = layer([geom, hole], kind=["a", "a"])
    cfg = Config()
    cfg.repair.auto_fill_max_area = 1
    cfg.repair.fill_if_same_class = True
    cfg.repair.class_field = "kind"
    cfg._repair_context = RepairContext(gdf, ["0", "1"])
    assert holes.repair("0", geom, {"kind": "a"}, cfg).status == "auto_accepted"
    assert holes.repair("0", geom, {"kind": "b"}, cfg).status == "rejected"
    # Whole-layer validation must roll back filling an occupied hole.
    bundle = repair_layer(gdf, cfg)
    event = next(r for r in bundle["records"] if r["action"] == "fill_holes")
    assert event["status"] == "rejected"
    assert bundle["accepted"].loc["0"].geometry.equals_exact(geom, 0)


def test_new_issue_rollback():
    hole = box(2, 2, 3, 3)
    geom = Polygon(box(0, 0, 10, 10).exterior, [hole.exterior])
    cfg = Config()
    cfg.rules.enable = ["HAS_HOLE"]  # Recheck still runs OVERLAP.
    cfg.repair.auto_fill_max_area = 2
    bundle = repair_layer(layer([geom, hole]), cfg)
    event = next(r for r in bundle["records"] if r["action"] == "fill_holes")
    assert event["status"] == "rejected"
    assert event["validation"]["new_findings"]
    assert bundle["accepted"].geometry.iloc[0].equals_exact(geom, 0)


def test_atomic_merge_and_staging(tmp_path):
    gdf = layer([box(0, 0, .5, 1), box(.5, 0, 10, 1)], kind=["a", "a"])
    cfg = Config()
    cfg.repair.class_field = "kind"
    bundle = repair_layer(gdf, cfg)
    assert len(bundle["accepted"]) == 1
    assert bundle["accepted"].geometry.iloc[0].area == 10
    assert gdf.geometry.iloc[0].area == .5
    payload = write_repair_outputs(bundle, tmp_path)
    assert payload["summary"]["auto_accepted"] == 1
    staging = gpd.read_file(tmp_path / "repair_staging.gpkg", layer="staging")
    assert staging.geometry.iloc[0] is None
    assert staging.geometry.iloc[1].area == 10
    assert staging.geom_original.str.startswith("POLYGON").all()
    assert staging.merged_into.iloc[0] == "1"
    assert json.loads((tmp_path / "repair_report.json").read_text(encoding="utf-8"))["repairs"]


def test_pending_unchanged(tmp_path):
    geom = touching()
    bundle = repair_layer(layer([geom]), Config())
    assert bundle["accepted"].geometry.iloc[0].equals_exact(geom, 0)
    assert any(r["status"] == "pending_review" for r in bundle["records"])
    write_repair_outputs(bundle, tmp_path)
    candidates = gpd.read_file(tmp_path / "repair_staging.gpkg", layer="candidates")
    assert candidates.geometry.iloc[0].is_valid


def test_duplicate_id_rejected():
    cfg = Config()
    cfg.input.id_field = "id"
    with pytest.raises(ValueError, match="唯一"):
        repair_layer(layer([box(0,0,1,1)]*2, id=["a", "a"]), cfg)


def test_cli_optional(sample_data, tmp_path):
    result = CliRunner().invoke(app, ["check", str(sample_data), "--layer", "sample", "--repair", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "repair_staging.gpkg").exists()
    assert (tmp_path / "repair_report.json").exists()


def test_geographic_manual():
    cfg = Config()
    cfg.is_geographic = True
    cfg.repair.touch_max_area_change = 3
    cfg.repair.touch_max_area_ratio = .1
    assert touch_island.repair("0", touching(), {}, cfg).status == "pending_review"


def test_sampling_forces_review():
    cfg = Config()
    cfg.rules.max_pairs = 1
    cfg.repair.touch_max_area_change = 3
    cfg.repair.touch_max_area_ratio = .1
    far = box(100, 0, 110, 10)
    bundle = repair_layer(layer([touching(), far, far, far]), cfg)
    event = next(r for r in bundle["records"] if r["action"] == "remove_touching_hole")
    assert event["status"] == "pending_review"
    assert "max_pairs" in event["reason"]


def test_no_touch_removal_for_near_hole():
    geom = Polygon(box(0, 0, 10, 10).exterior, [box(.0005, 2, 2, 4).exterior])
    proposal = touch_island.repair("0", geom, {}, Config())
    assert proposal.status == "rejected"
    assert proposal.repaired_geom.equals_exact(geom, 0)


def test_empty_layer_output(tmp_path):
    bundle = repair_layer(layer([]), Config())
    write_repair_outputs(bundle, tmp_path)
    assert (tmp_path / "repair_staging.gpkg").exists()


def test_pending_cannot_be_merge_target():
    cfg = Config()
    cfg.repair.class_field = "kind"
    # Protected hole edit must not be bypassed by a later merge into that face.
    bundle = repair_layer(layer([touching(), box(10, 0, 10.5, 1)], kind=["a", "a"]), cfg)
    assert len(bundle["accepted"]) == 2
    assert bundle["accepted"].geometry.iloc[0].equals_exact(touching(), 0)
