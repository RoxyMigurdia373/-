import base64
import json
import time

import pytest

from vectopo_check.cloud import component_html, dispatch
from vectopo_check.progress import progress_percent
from vectopo_check.web import Workspace


def test_cloud_upload_process_repeat_download_and_session_isolation():
    workspace, other = Workspace(), Workspace()
    try:
        source = {"type": "FeatureCollection", "crs": {"type":"name", "properties":{"name":"EPSG:32650"}},
                  "features":[{"type":"Feature", "properties":{"name":"small"},
                    "geometry":{"type":"Polygon", "coordinates":[[[0,0],[5,0],[5,10],[0,10],[0,0]]]}}]}
        uploaded = dispatch(workspace, {"path":"/api/upload", "data":{"files":[
            {"name":"test.geojson", "data":base64.b64encode(json.dumps(source).encode()).decode()}]}})
        request = {"path":"/api/check", "data":{"dataset":uploaded["dataset"], "layer":uploaded["layers"][0],
                   "repair":True, "simple_mode":True}}
        job = dispatch(workspace, request)["job"]
        deadline = time.monotonic()+30
        while time.monotonic()<deadline:
            result = dispatch(workspace, {"path":f"/api/jobs/{job}"})
            if result["status"] in {"done", "error"}:
                break
            time.sleep(.02)
        assert result["status"] == "done", result
        assert result["progress"] == 100
        assert not result["repair_error"]
        assert dispatch(workspace, request) == {"job":job, "reused":True}
        file = dispatch(workspace, {"path":f"/api/jobs/{job}/processed_shp.zip"})
        assert base64.b64decode(file["base64"]).startswith(b"PK")
        with pytest.raises(ValueError):
            dispatch(other, {"path":f"/api/jobs/{job}"})
        with pytest.raises(ValueError):
            dispatch(workspace, {"path":f"/api/jobs/{job}/secrets.toml"})
    finally:
        workspace.close()
        other.close()


def test_stage_progress_and_original_layout():
    messages = ["正在读取矢量数据…", "正在自动选择米制坐标系", "正在检查图斑：500 / 1000",
                "正在建立空间索引", "正在检查 OVERLAP：500 / 1000 对", "正在写入报告", "正在导出带问题",
                "正在处理 INVALID_HOLE：500 / 1000", "正在处理 TOUCH_ISLAND：500 / 1000",
                "正在处理 SMALL_AREA：500 / 1000", "正在写入 processed / staging 成果图层"]
    values = [progress_percent(message) for message in messages]
    assert values == sorted(values)
    assert max(values)<100
    html = component_html()
    for element in ['class="layout"', 'id="map"', 'id="issues"', 'id="task-progress"', 'streamlit:componentReady']:
        assert element in html
    assert "数据留在本机" not in html
