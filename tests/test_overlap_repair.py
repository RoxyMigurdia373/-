import geopandas as gpd
from shapely.geometry import box

from vectopo_check.config import Config
from vectopo_check.engine import check_layer
from vectopo_check.preset import prepare_parcels, SMALL_AREA_M2
from vectopo_check.repair.overlap import find_overlaps
from vectopo_check.repair.workflow import repair_layer, write_repair_outputs


def run(geometries, cfg=None, **attrs):
    frame = gpd.GeoDataFrame(attrs, geometry=geometries, crs=32649)
    frame, cfg, _ = prepare_parcels(frame, cfg)
    return frame, cfg, repair_layer(frame, cfg)


def overlaps(bundle, cfg):
    return find_overlaps(bundle["accepted"], cfg)[0]


def test_smaller_claim_is_clipped_and_layer_stays_disjoint():
    frame, cfg, bundle = run([box(0, 0, 20, 20), box(10, 0, 26, 20)])
    assert frame.area.tolist() == [400.0, 320.0]
    assert overlaps(bundle, cfg) == []
    assert bundle["verification"]["passed"]
    assert bundle["verification"]["overlap_pairs"] == 0
    assert sorted(bundle["accepted"].area) == [120.0, 400.0]
    event = next(r for r in bundle["records"] if r["issue_type"] == "OVERLAP")
    assert event["action"] == "clip_overlap" and event["status"] == "auto_accepted"
    assert event["feature_id"] == "1" and event["target_feature_id"] is None
    assert event["diff"]["overlap_area"] == 200.0
    assert event["diff"]["removed_area"] == 200.0
    assert event["diff"]["kept_ratio"] == 0.375
    # The processed layer really is free of OVERLAP findings, not just recorded as such.
    assert "OVERLAP" not in check_layer(bundle["accepted"], cfg).summary


def test_remainder_below_threshold_is_merged_instead_of_shrunk():
    frame, cfg, bundle = run([box(0, 0, 20, 20), box(18, 0, 22, 20)])
    assert frame.area.tolist() == [400.0, 80.0]
    assert frame.area.iloc[1] >= SMALL_AREA_M2  # not a small-area candidate beforehand
    assert len(bundle["accepted"]) == 1
    assert bundle["accepted"].area.iloc[0] == 440.0
    assert bundle["verification"]["passed"]
    event = next(r for r in bundle["records"] if r["issue_type"] == "OVERLAP")
    assert event["action"] == "merge_overlap" and event["status"] == "auto_accepted"
    assert event["target_feature_id"] == "0"
    assert event["diff"]["consumed_area"] == 80.0
    assert abs(event["diff"]["conservation_error"]) < 1e-9


def test_fully_contained_face_is_absorbed_and_exported_as_merged(tmp_path):
    _, cfg, bundle = run([box(0, 0, 20, 20), box(5, 5, 10, 10)], owner=["target", "absorbed"])
    assert len(bundle["accepted"]) == 1
    assert bundle["accepted"].area.iloc[0] == 400.0
    assert bundle["merged_into"] == {"1": "0"}
    assert bundle["verification"]["passed"]
    write_repair_outputs(bundle, tmp_path)
    stage = gpd.read_file(tmp_path / "repair_staging.gpkg", layer="staging")
    merged = stage.loc[stage.repair_id == "1"].iloc[0]
    assert merged.repair_status == "merged" and merged.merged_into == "0"
    assert merged.owner == "absorbed" and merged.geometry is None
    assert merged.geom_original is not None
    processed = gpd.read_file(tmp_path / "repair_staging.gpkg", layer="processed")
    assert len(processed) == 1 and processed.area.iloc[0] == 400.0


def test_equal_area_duplicate_keeps_the_earlier_row():
    _, cfg, bundle = run([box(0, 0, 10, 10), box(0, 0, 10, 10)], owner=["first", "second"])
    assert len(bundle["accepted"]) == 1
    assert list(bundle["accepted"].index) == ["0"]
    assert bundle["merged_into"] == {"1": "0"}
    assert overlaps(bundle, cfg) == []
    event = next(r for r in bundle["records"] if r["issue_type"] == "OVERLAP")
    assert event["feature_id"] == "1" and event["target_feature_id"] == "0"


def test_three_face_chain_resolves_in_one_run():
    _, cfg, bundle = run([box(0, 0, 10, 10), box(5, 0, 25, 10), box(20, 0, 40, 10)])
    assert bundle["verification"]["passed"]
    assert bundle["verification"]["overlap_rounds"] == 1
    assert overlaps(bundle, cfg) == []
    assert sorted(bundle["accepted"].area) == [150.0, 250.0]


def test_equal_area_chain_keeps_the_priority_fixed_while_clipping():
    # Regression: the first clip shrinks C2 from 200 m² to 100 m². If the ranking
    # were recomputed from live areas, C3 (200 m²) would then outrank C2 and the
    # C2/C3 overlap would never be yielded, leaving a residual pair behind.
    _, cfg, bundle = run([box(600, 0, 620, 10), box(610, 0, 630, 10), box(620, 0, 640, 10)])
    assert bundle["verification"]["passed"]
    assert bundle["verification"]["overlap_pairs"] == 0
    assert overlaps(bundle, cfg) == []
    assert bundle["accepted"].area.tolist() == [200.0, 100.0, 100.0]
    clipped = [r for r in bundle["records"] if r["action"] == "clip_overlap"]
    assert sorted(r["feature_id"] for r in clipped) == ["1", "2"]
    assert all(r["status"] == "auto_accepted" for r in clipped)


def test_impossible_overlap_is_reported_not_declared_clean():
    # Plugging the notch of the U shape would enclose a void, so absorbing the
    # small claim is impossible and trimming leaves a sub-threshold parcel:
    # the honest answer is "needs review", not a clean verdict.
    target = box(0, 0, 100, 100).difference(box(40, 40, 60, 100))
    source = box(38, 99, 62, 101)
    _, cfg, bundle = run([target, source])
    assert not bundle["verification"]["passed"]
    assert bundle["verification"]["overlap_pairs"] == 1
    assert bundle["verification"]["remaining_overlap"] == 1
    event = next(r for r in bundle["records"] if r["issue_type"] == "OVERLAP")
    assert event["status"] == "pending_review" and event["action"] == "clip_overlap"
    assert "重叠" in event["reason"]
    residual = bundle["verification"]["remaining"]
    assert residual[0]["feature_id"] == "1" and "OVERLAP" in residual[0]["issues"]
    assert residual[0]["overlap_pairs"][0]["related_feature_id"] == "0"
    assert residual[0]["reason"] == event["reason"]

    # No accepted change was applied to that pair, so both faces are still present.
    assert len(bundle["accepted"]) == 2


def test_disjoint_layer_records_no_overlap_events():
    _, cfg, bundle = run([box(0, 0, 10, 10), box(10, 0, 20, 10), box(100, 0, 110, 10)])
    assert not [r for r in bundle["records"] if r["issue_type"] == "OVERLAP"]
    assert bundle["verification"]["passed"]
    assert bundle["verification"]["overlap_pairs"] == 0
    assert len(bundle["accepted"]) == 3


def test_default_config_mode_clips_and_keeps_both_rows():
    cfg = Config()
    cfg.tolerance.min_area = 1.0
    frame = gpd.GeoDataFrame({"pid": ["a", "b"]}, geometry=[box(0, 0, 10, 10), box(9, 0, 16, 10)], crs=32649)
    frame.index = ["a", "b"]
    bundle = repair_layer(frame, cfg)
    assert sorted(bundle["accepted"].area) == [60.0, 100.0]
    assert len(bundle["accepted"]) == 2
    assert bundle["verification"]["passed"]
    event = next(r for r in bundle["records"] if r["issue_type"] == "OVERLAP")
    assert event["action"] == "clip_overlap" and event["feature_id"] == "b"


def test_second_pass_over_repaired_layer_is_a_no_op():
    _, cfg, first = run([box(0, 0, 20, 20), box(18, 0, 22, 20), box(100, 0, 110, 10)])
    assert len(first["accepted"]) == 2
    second = repair_layer(first["accepted"], cfg)
    assert not second["records"]
    assert second["verification"]["passed"]
    assert second["verification"]["overlap_pairs"] == 0
    assert sorted(second["accepted"].area) == sorted(first["accepted"].area)


def test_merging_across_classes_requires_review():
    cfg = Config()
    cfg.tolerance.min_area = 1.0
    cfg.repair.class_field = "owner"
    cfg.repair.merge_without_class = False
    frame = gpd.GeoDataFrame({"owner": ["left", "right"]},
                            geometry=[box(0, 0, 20, 20), box(18, 0, 19, 20)], crs=32649)
    frame.index = ["left", "right"]
    # The 20 ㎡ overlap leaves 20 ㎡ behind: too small to keep, so it must merge.
    bundle = repair_layer(frame, cfg)
    event = next(r for r in bundle["records"] if r["issue_type"] == "OVERLAP")
    assert event["action"] == "merge_overlap" and event["status"] == "pending_review"
    assert "分类" in event["reason"]
    # Review means not applied: both claims stay and the overlap keeps showing up.
    assert len(bundle["accepted"]) == 2
    assert not bundle["verification"]["passed"]
    assert bundle["verification"]["overlap_pairs"] == 1
