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


def test_chunked_upload_roundtrip_and_validation():
    from vectopo_check.cloud_transfer import CHUNK_SIZE, MAX_UPLOAD
    workspace, outsider = Workspace(), Workspace()
    try:
        source = {"type":"FeatureCollection", "features":[], "padding":"x"*(CHUNK_SIZE+100)}
        payload = json.dumps(source).encode()
        start = dispatch(workspace, {"path":"/api/upload/begin", "data":{"files":[{"name":"sample.geojson", "size":len(payload)}]}})
        token = start["upload"]
        path = "/api/upload/chunk"
        with pytest.raises(ValueError, match="顺序"):
            dispatch(workspace, {"path":path, "data":{"upload":token,"index":0,"offset":1,"base64":"eA=="}})
        with pytest.raises(ValueError, match="失效"):
            dispatch(outsider, {"path":path, "data":{"upload":token,"index":0,"offset":0,"base64":"eA=="}})
        with pytest.raises(ValueError, match="完整"):
            dispatch(workspace, {"path":"/api/upload/finish", "data":{"upload":token}})
        for offset in range(0,len(payload),CHUNK_SIZE):
            dispatch(workspace, {"path":path, "data":{"upload":token,"index":0,"offset":offset,
                "base64":base64.b64encode(payload[offset:offset+CHUNK_SIZE]).decode()}})
        result = dispatch(workspace, {"path":"/api/upload/finish", "data":{"upload":token}})
        assert workspace.datasets[result["dataset"]].read_bytes() == payload
        for files in [[{"name":"../bad.zip","size":1}], [{"name":"big.zip","size":MAX_UPLOAD+1}],
                      [{"name":"same.shp","size":1},{"name":"SAME.SHP","size":1}]]:
            with pytest.raises(ValueError):
                dispatch(workspace, {"path":"/api/upload/begin", "data":{"files":files}})
    finally:
        workspace.close()
        outsider.close()


def test_chunked_download_roundtrip():
    from vectopo_check.cloud_transfer import CHUNK_SIZE
    workspace = Workspace()
    try:
        workspace.jobs['testjob'] = {'status':'done'}
        folder = workspace.root/'testjob'
        folder.mkdir()
        payload = b'abcd'*(CHUNK_SIZE//4+10)
        (folder/'report.json').write_bytes(payload)
        path = '/api/jobs/testjob/report.json'
        metadata = dispatch(workspace, {'path':path,'data':{'metadata':True}})
        assert metadata['size'] == len(payload)
        chunks = [base64.b64decode(dispatch(workspace, {'path':path,'data':{'offset':i}})['base64'])
                  for i in range(0,len(payload),CHUNK_SIZE)]
        assert b''.join(chunks) == payload
        with pytest.raises(ValueError):
            dispatch(workspace, {'path':path,'data':{'offset':-1}})
    finally:
        workspace.close()
