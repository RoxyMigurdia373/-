"""Desktop window tests: the native front end, no browser and no HTTP."""

from __future__ import annotations

from zipfile import ZIP_DEFLATED, ZipFile

import geopandas as gpd
import pytest
from shapely.geometry import box

from vectopo_check.desktop import DesktopApp
from vectopo_check.pipeline import Cancelled


@pytest.fixture
def root():
    tkinter = pytest.importorskip("tkinter")
    try:
        widget = tkinter.Tk()
    except Exception as exc:  # no display / restricted session
        pytest.skip(f"Tk 不可用：{exc}")
    widget.withdraw()
    try:
        yield widget
    finally:
        try:
            widget.destroy()
        except Exception:
            pass


def make_dataset(tmp_path):
    frame = gpd.GeoDataFrame({"name": ["a", "b"]},
                             geometry=[box(0, 0, 20, 20), box(10, 0, 30, 20)], crs="EPSG:32650")
    path = tmp_path / "parcels.gpkg"
    frame.to_file(path, layer="parcels", driver="GPKG", engine="pyogrio")
    return path


def test_selecting_a_file_fills_layers_and_output_dir(root, tmp_path):
    path = make_dataset(tmp_path)
    app = DesktopApp(root)
    try:
        app.set_path(path)
        root.update()
        assert app.source == path
        assert app.layer_var.get() == "parcels"
        assert str(app.layer_box.cget("state")) == "readonly"
        assert app.out_var.get().endswith("parcels_成果")
        assert str(app.start_button.cget("state")) == "normal"
    finally:
        app.close()


def test_run_fills_tables_and_enables_exports(root, tmp_path):
    path = make_dataset(tmp_path)
    app = DesktopApp(root)
    try:
        app.set_path(path)
        payload = app.run_blocking()
        root.update()

        assert payload["report"].feature_count == 2
        summary = [app.summary_tree.item(item, "values") for item in app.summary_tree.get_children()]
        assert summary[0][0] == "图斑数" and summary[0][1] == "2"
        assert any(row[0] == "OVERLAP" and row[1] == "1" for row in summary)
        issues = [app.issue_tree.item(item, "values") for item in app.issue_tree.get_children()]
        assert any(row[0] == "OVERLAP" for row in issues)
        assert app.progress_var.get() == 100.0
        assert str(app.export_buttons["checked"].cget("state")) == "normal"
        assert str(app.export_buttons["repaired"].cget("state")) == "normal"
        assert str(app.folder_button.cget("state")) == "normal"
        assert (app.run_dir / "repair_staging.gpkg").is_file()
        text = app.log_view.get("1.0", "end")
        assert "完成" in text and "处理后复核" in text
        assert "用时" in app.status_var.get()
    finally:
        app.close()


def test_advanced_mode_keeps_raw_thresholds(root, tmp_path):
    path = make_dataset(tmp_path)
    app = DesktopApp(root)
    try:
        app.set_path(path)
        app.simple_var.set(False)
        payload = app.run_blocking()
        root.update()
        assert payload["cfg"].simple_mode is False
        assert payload["preset"] is None
        assert "高级模式" in app.mode_hint.cget("text")
    finally:
        app.close()


def test_zip_shp_bundle_is_accepted(root, tmp_path):
    frame = gpd.GeoDataFrame({"name": ["a", "b"]},
                             geometry=[box(0, 0, 20, 20), box(10, 0, 30, 20)], crs="EPSG:32650")
    shp = tmp_path / "parcels.shp"
    frame.to_file(shp, driver="ESRI Shapefile", engine="pyogrio")
    archive = tmp_path / "parcels.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
        for sidecar in tmp_path.glob("parcels.*"):
            if sidecar.suffix.lower() in {".shp", ".shx", ".dbf", ".prj", ".cpg"}:
                bundle.write(sidecar, sidecar.name)

    app = DesktopApp(root)
    try:
        app.set_path(archive)
        root.update()
        assert app.source.suffix == ".shp"
        assert app.layer_var.get() == "dataset"
        assert "共 1 个图层" in app.layer_hint.cget("text")
        payload = app.run_blocking()
        assert payload["report"].feature_count == 2
    finally:
        app.close()


def test_cancelled_run_leaves_the_window_clean(root, tmp_path):
    app = DesktopApp(root)
    try:
        app.set_path(make_dataset(tmp_path))
        app.cancel.set()
        with pytest.raises(Cancelled):
            app.run_blocking(cancel=app.cancel.is_set)
        assert app.progress_var.get() == 0.0
        assert app.run_dir is None
        assert app.status_var.get() == "已选择数据，点“开始处理”"
    finally:
        app.close()


def test_start_clears_a_stale_cancel_flag(root, tmp_path, monkeypatch):
    import vectopo_check.desktop as desktop_module

    seen = {}

    def fake_pipeline(*args, **kwargs):
        seen["cancel"] = kwargs.get("cancel")
        return {"report": None, "output_dir": str(tmp_path / "fake"), "repair_summary": None,
                "repair_verification": None, "preset": None, "repair_error": None,
                "vector_export_error": None}

    monkeypatch.setattr(desktop_module, "run_pipeline", fake_pipeline)
    app = DesktopApp(root)
    try:
        app.set_path(make_dataset(tmp_path))
        app.cancel.set()
        app.start()
        app.worker.join(5)
        root.update()
        assert seen["cancel"] is not None and seen["cancel"]() is False
    finally:
        app.close()


def test_missing_file_is_reported_without_a_traceback(root):
    app = DesktopApp(root)
    try:
        app.set_path(root.winfo_screenwidth() and "不存在的图层.gpkg")
        assert "不存在" in app.log_view.get("1.0", "end")
        assert app.source is None
    finally:
        app.close()


def test_stale_progress_does_not_override_the_final_message(root, tmp_path):
    app = DesktopApp(root)
    try:
        app.set_path(make_dataset(tmp_path))
        app.run_blocking()
        app.queue.put(("progress", "正在检查图斑：1 / 2"))
        app._pump()
        assert app.message_var.get() == "处理完成"
        assert app.progress_var.get() == 100.0
    finally:
        app.close()
