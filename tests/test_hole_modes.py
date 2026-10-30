"""岛洞处理方式：细缝 / 1 厘米开缝 / 零宽切分 / 填平 / 不处理。"""

import geopandas as gpd
import pytest
from shapely.geometry import Polygon, box

from vectopo_check.config import Config
from vectopo_check.desktop import HOLE_CHOICES, HOLE_HINTS, DesktopApp
from vectopo_check.pipeline import run_pipeline
from vectopo_check.preset import prepare_parcels
from vectopo_check.repair import hole_split
from vectopo_check.repair.workflow import repair_layer

SHELL = 400.0
HOLE = 16.0
DONUT_AREA = SHELL - HOLE


def donut():
    """20×20 面，中间 4×4 的洞，面积 384 m²。"""
    return Polygon(box(0, 0, 20, 20).exterior, [box(8, 8, 12, 12).exterior])


def frame_of(*geometries, crs=32650):
    return gpd.GeoDataFrame({"name": [f"p{i}" for i in range(len(geometries))]},
                            geometry=list(geometries), crs=crs)


def config_for(hole_action, simple=True):
    cfg = Config()
    cfg.simple_mode = simple
    cfg.repair.hole_action = hole_action
    cfg.tolerance.min_area = 0.0
    return cfg


def only_record(bundle, action):
    events = [r for r in bundle["records"] if r["action"] == action]
    assert len(events) == 1, [r["action"] for r in bundle["records"]]
    return events[0]


@pytest.mark.parametrize("action,lost_range", [("slit", (0.0, 0.01)), ("cut_open", (0.05, 0.3))])
def test_micro_channel_keeps_one_parcel_and_loses_almost_nothing(action, lost_range):
    bundle = repair_layer(frame_of(donut()), config_for(action))
    event = only_record(bundle, "slit_open_holes" if action == "slit" else "cut_open_holes")
    assert event["status"] == "auto_accepted", event["reason"]
    result = bundle["accepted"].geometry.iloc[0]
    assert len(bundle["accepted"]) == 1
    assert result.is_valid and not result.interiors
    lost = DONUT_AREA - result.area
    assert lost_range[0] <= lost <= lost_range[1], lost
    assert event["diff"]["hole_count_after"] == 0


def test_slit_loses_strictly_less_than_the_one_centimetre_channel():
    slit = repair_layer(frame_of(donut()), config_for("slit"))["accepted"].geometry.iloc[0].area
    cut = repair_layer(frame_of(donut()), config_for("cut_open"))["accepted"].geometry.iloc[0].area
    assert DONUT_AREA - slit < (DONUT_AREA - cut) / 10


def test_split_open_is_exactly_area_preserving():
    bundle = repair_layer(frame_of(donut()), config_for("split_open"))
    event = only_record(bundle, "split_open_holes")
    assert event["status"] == "auto_accepted", event["reason"]
    parts = bundle["accepted"]
    assert len(parts) == 2, list(parts.index)
    assert all(part.is_valid and not part.interiors for part in parts.geometry)
    assert parts.area.sum() == pytest.approx(DONUT_AREA, abs=1e-9)
    assert event["diff"]["area_change"] == pytest.approx(0.0, abs=1e-9)
    assert event["diff"]["removed_area"] == 0.0
    assert event["child_ids"] == [f"{event['feature_id']}#hole1", f"{event['feature_id']}#hole2"]
    # 切分出的两块共用切线，不应互相压盖。
    assert parts.geometry.iloc[0].intersection(parts.geometry.iloc[1]).area == pytest.approx(0.0, abs=1e-9)


def test_split_open_leaves_a_clean_layer_for_the_followup_phases():
    bundle = repair_layer(frame_of(donut()), config_for("split_open"))
    assert bundle["verification"]["passed"]
    assert bundle["accepted"].geometry.is_valid.all()


@pytest.mark.parametrize("simple", [True, False])
@pytest.mark.parametrize("action", ["slit", "cut_open"])
def test_narrow_channel_is_accepted_in_both_modes(action, simple):
    # 开缝必然在图斑自身留下两条极短边，复检只对该图斑豁免这类新增发现。
    bundle = repair_layer(frame_of(donut()), config_for(action, simple=simple))
    event = only_record(bundle, "slit_open_holes" if action == "slit" else "cut_open_holes")
    assert event["status"] == "auto_accepted", event["reason"]
    result = bundle["accepted"].geometry.iloc[0]
    assert result.is_valid and not result.interiors
    assert bundle["accepted"].area.iloc[0] < DONUT_AREA


def test_fill_absorbs_the_hole():
    bundle = repair_layer(frame_of(donut()), config_for("fill"))
    event = only_record(bundle, "fill_holes")
    assert event["status"] == "auto_accepted", event["reason"]
    result = bundle["accepted"].geometry.iloc[0]
    assert len(bundle["accepted"]) == 1
    assert not result.interiors
    assert result.area == pytest.approx(SHELL, abs=1e-9)
    assert event["diff"]["filled_area"] == pytest.approx(HOLE, abs=1e-9)


def test_legacy_leaves_the_hole_alone():
    bundle = repair_layer(frame_of(donut()), config_for("legacy"))
    assert [r for r in bundle["records"] if r["issue_type"] in {"HAS_HOLE", "INVALID_HOLE"}] == []
    result = bundle["accepted"].geometry.iloc[0]
    assert len(result.interiors) == 1
    assert result.area == pytest.approx(DONUT_AREA, abs=1e-9)


def test_preset_default_is_the_hairline_slit():
    _, cfg, info = prepare_parcels(frame_of(donut()))
    assert cfg.repair.hole_action == "slit"
    assert info["hole_action"] == "slit"
    assert info["slit_width_m"] == cfg.repair.slit_width


def test_hole_split_open_faces_api():
    faces = hole_split.open_faces(donut())
    assert faces is not None and len(faces) == 2
    assert sum(face.area for face in faces) == pytest.approx(DONUT_AREA, abs=1e-9)


def test_pipeline_applies_the_chosen_mode_after_the_preset(tmp_path):
    source = tmp_path / "donut.gpkg"
    frame_of(donut()).to_file(source, layer="parcels", driver="GPKG")
    payload = run_pipeline(source, "parcels", cfg=Config(), output_dir=tmp_path / "out",
                           repair=True, hole_action="split_open")
    assert payload["cfg"].repair.hole_action == "split_open"
    processed = gpd.read_file(tmp_path / "out" / "repair_staging.gpkg", layer="processed")
    assert len(processed) == 2
    assert processed.area.sum() == pytest.approx(DONUT_AREA, abs=1e-6)
    assert not any(geom.interiors for geom in processed.geometry)


def test_pipeline_rejects_an_unknown_mode(tmp_path):
    source = tmp_path / "donut.gpkg"
    frame_of(donut()).to_file(source, layer="parcels", driver="GPKG")
    with pytest.raises(ValueError, match="岛洞处理方式"):
        run_pipeline(source, "parcels", output_dir=tmp_path / "out", hole_action="teleport")


def test_cli_hole_action_flag(tmp_path):
    from typer.testing import CliRunner

    from vectopo_check.cli import app

    source = tmp_path / "donut.gpkg"
    frame_of(donut()).to_file(source, layer="parcels", driver="GPKG")
    out = tmp_path / "out"
    result = CliRunner().invoke(app, ["check", str(source), "--layer", "parcels", "--simple", "--repair",
                                      "--hole-action", "split_open", "--out", str(out)])
    assert result.exit_code == 0, result.output
    processed = gpd.read_file(out / "repair_staging.gpkg", layer="processed")
    assert len(processed) == 2
    assert processed.area.sum() == pytest.approx(DONUT_AREA, abs=1e-6)
    bad = CliRunner().invoke(app, ["check", str(source), "--layer", "parcels", "--hole-action", "nope",
                                   "--out", str(tmp_path / "bad")])
    assert bad.exit_code == 2
    assert "岛洞处理方式" in bad.output


def test_window_offers_every_mode_and_passes_it_on(tmp_path):
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except Exception as exc:  # pragma: no cover - headless CI
        pytest.skip(f"Tk 不可用：{exc}")
    root.withdraw()
    try:
        app = DesktopApp(root)
        assert [value for value, _ in HOLE_CHOICES] == ["slit", "cut_open", "split_open", "fill", "legacy"]
        assert app.hole_var.get() == "slit"
        assert "细缝" in app.hole_hint.cget("text")
        app.hole_var.set("fill")
        assert "填平" in app.hole_hint.cget("text")
        assert app._config(tmp_path).repair.hole_action == "fill"
        source = tmp_path / "donut.gpkg"
        frame_of(donut()).to_file(source, layer="parcels", driver="GPKG")
        app.hole_var.set("split_open")
        app.set_path(source)
        payload = app.run_blocking(output_dir=tmp_path / "out", repair=True)
        assert payload["cfg"].repair.hole_action == "split_open"
        assert len(gpd.read_file(tmp_path / "out" / "repair_staging.gpkg", layer="processed")) == 2
    finally:
        root.destroy()
