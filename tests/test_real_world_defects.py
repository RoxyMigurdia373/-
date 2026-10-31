"""Regressions found on a real cadastral parcel layer (石咀镇, EPSG:4326)."""
from collections import Counter

import geopandas as gpd
import pytest
from shapely import wkt
from shapely.geometry import box

from vectopo_check.config import Config
from vectopo_check.engine import check_layer
from vectopo_check.preset import prepare_parcels
from vectopo_check.repair.base import bridge_gap
from vectopo_check.repair.workflow import regression, repair_layer

# One real parcel whose interior ring meets the shell at a single point: GEOS
# reports "Ring Self-intersection" and the island is invisible to the hole check.
SELF_TOUCH = (
    "POLYGON ((112.16471179650797 23.595786505673246, 112.16472609791781 23.595844644435516, "
    "112.16469937127174 23.595878263355083, 112.16465172019272 23.595831458218022, "
    "112.16462929167169 23.595752649693868, 112.16463947005303 23.595736812503638, "
    "112.1646839560698 23.595742914848284, 112.16469685067264 23.595782963282073, "
    "112.16471179650797 23.595786505673246, 112.1646492168388 23.59566611189088, "
    "112.16456840573137 23.595696605013032, 112.16448520544472 23.59576536439819, "
    "112.16451150760933 23.59583633478965, 112.16452134263804 23.59597821259006, "
    "112.16452179798277 23.595978081882645, 112.16461768034611 23.59597808238061, "
    "112.16467972203317 23.595978081130024, 112.16472484765333 23.595983720950183, "
    "112.16476432959738 23.595995002172124, 112.16476805670257 23.595910056669883, "
    "112.16471179650797 23.595786505673246))"
)


def prepare(geometries, crs=32650):
    frame = gpd.GeoDataFrame(geometry=geometries, crs=crs)
    return prepare_parcels(frame, Config())


def test_self_touching_island_is_reported_and_opened():
    prepared, cfg, _ = prepare([wkt.loads(SELF_TOUCH)], crs=4326)
    geom = prepared.geometry.iloc[0]
    assert not geom.is_valid
    assert "Self-intersection" in _validity(geom)

    report = check_layer(prepared, cfg, input_path="<memory>")
    assert report.summary.get("INVALID_HOLE") == 1

    bundle = repair_layer(prepared, cfg)
    assert bundle["verification"]["passed"] is True
    assert bundle["verification"]["remaining_invalid"] == 0
    repaired = bundle["accepted"].geometry.iloc[0]
    assert repaired.is_valid and not repaired.interiors
    assert repaired.area == pytest.approx(geom.area, rel=1e-6)
    record = bundle["records"][0]
    assert record["action"] == "slit_open_holes"
    assert record["status"] == "auto_accepted"
    assert record["diff"]["healed_self_touch"] is True


def _validity(geom):
    from shapely.validation import explain_validity
    return explain_validity(geom)


def test_identical_stacked_parcels_resolve_without_float_noise():
    prepared, cfg, _ = prepare([box(0, 0, 10, 10)] * 3)
    bundle = repair_layer(prepared, cfg)
    assert [(r["action"], r["status"]) for r in bundle["records"]] == [
        ("merge_overlap", "auto_accepted"), ("merge_overlap", "auto_accepted")]
    assert len(bundle["accepted"]) == 1
    assert bundle["verification"]["passed"] is True
    assert bundle["verification"]["overlap_pairs"] == 0


def test_hairline_seam_is_merged_instead_of_deleted():
    # 0.3 mm between two separately digitised parcels used to hide the neighbour,
    # so the 9 m² parcel was reported as isolated and deleted.
    geometries = [box(0, 0, 3, 3), box(3.0003, 0, 20, 20)]
    prepared, cfg, _ = prepare(geometries)
    bundle = repair_layer(prepared, cfg)
    actions = [r["action"] for r in bundle["records"]]
    assert actions[0] == "merge_small_area"
    assert "delete_isolated_small_area" not in actions
    assert len(bundle["accepted"]) == 1
    assert bundle["accepted"].geometry.iloc[0].area == pytest.approx(9 + 16.9997 * 20, rel=1e-6)
    assert bundle["verification"]["passed"] is True
    assert bundle["verification"]["remaining_small_area"] == 0


def test_corner_contact_is_still_not_a_shared_edge():
    prepared, cfg, _ = prepare([box(0, 0, 3, 3), box(3, 3, 20, 20)])
    bundle = repair_layer(prepared, cfg)
    assert bundle["records"][0]["action"] == "delete_isolated_small_area"
    assert len(bundle["accepted"]) == 1


def test_bridge_gap_opens_a_zero_width_pinch():
    merged = box(0, 0, 10, 10).union(box(10, 10, 20, 20))
    assert merged.geom_type == "MultiPolygon"
    bridged = bridge_gap(merged, 0.001)
    assert bridged is not None and bridged.geom_type == "Polygon" and bridged.is_valid
    assert bridged.area == pytest.approx(merged.area, abs=1e-6)
    far_apart = box(0, 0, 1, 1).union(box(5, 5, 6, 6))
    assert bridge_gap(far_apart, 0.001) is None


def test_float_noise_in_overlap_does_not_count_as_growth():
    before = (Counter(), {("a", "b"): 851.9875386901153})
    noise = (Counter(), {("a", "b"): 851.9875386901156})
    grown, detail = regression(before, noise, overlap_margin=1e-6)
    assert grown is False and detail["increased_overlaps"] == []
    real = (Counter(), {("a", "b"): 851.99})
    grown, detail = regression(before, real, overlap_margin=1e-6)
    assert grown is True and detail["increased_overlaps"] == [("a", "b")]
