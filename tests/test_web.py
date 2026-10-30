import base64
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from vectopo_check.web import Workspace, make_handler


@pytest.fixture
def web_server():
    workspace = Workspace()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(workspace))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", workspace
    server.shutdown()
    server.server_close()
    thread.join()
    workspace.close()


def request(base, path, payload=None, headers=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(base + path, data=data, headers=headers or {})
    return urllib.request.urlopen(req, timeout=10)


def test_web_full_flow(web_server, sample_data):
    base, _ = web_server
    assert "面要素拓扑工作台" in request(base, "/").read().decode()
    cfg = json.load(request(base, "/api/config"))
    uploaded = json.load(request(base, "/api/upload", {"files": [{"name": "sample.gpkg", "data": base64.b64encode(sample_data.read_bytes()).decode()}]}))
    assert uploaded["layers"] == ["sample"]
    started = json.load(request(base, "/api/check", {"dataset": uploaded["dataset"], "layer": "sample", "config": cfg, "repair": True}))
    for _ in range(100):
        result = json.load(request(base, f"/api/jobs/{started['job']}"))
        if result["status"] not in {"queued", "running"}:
            break
        time.sleep(.02)
    assert result["status"] == "done", result
    assert result["report"]["feature_count"] == 4
    assert len(result["coverage"]["features"]) == 4
    assert result["report"]["input_path"] == "sample.gpkg"
    assert result["vector_export_error"] is None
    assert result["vector_fields"]["topo_issue"] == "topo_issue"
    assert result["repair_enabled"] and result["repair_error"] is None
    for name in ["report.json", "issues.geojson", "report.csv", "summary.txt", "checked_features.gpkg", "check_reports.zip", "repair_report.json", "repair_staging.gpkg", "checked_features_shp.zip", "processed_shp.zip"]:
        response = request(base, f"/api/jobs/{started['job']}/{name}")
        assert response.read()
        assert name in response.headers["Content-Disposition"]


@pytest.mark.parametrize("files", [[], [{"name": "../x.geojson", "data": "e30="}], [{"name": "evil.exe", "data": ""}], [{"name": "bad.geojson", "data": "??"}], [{"name": "a.shp", "data": "e30="}]])
def test_bad_uploads(web_server, files):
    base, _ = web_server
    with pytest.raises(urllib.error.HTTPError) as err:
        request(base, "/api/upload", {"files": files})
    assert err.value.code == 400


def test_web_errors_and_config(web_server):
    base, _ = web_server
    cfg = json.load(request(base, "/api/config", {"yaml": "tolerance:\n  min_area: 3\nrules:\n  enable: [OVERLAP]"}))
    assert cfg["tolerance"]["min_area"] == 3
    with pytest.raises(urllib.error.HTTPError) as err:
        request(base, "/api/check", {"dataset": "missing"})
    assert err.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as err:
        request(base, "/api/config", {"yaml": "tolerance:\n  distance: -2"})
    assert err.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as err:
        request(base, "/api/config", {}, {"Origin": "https://example.com"})
    assert err.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as err:
        request(base, "/api/config", None, {"Host": "evil.example"})
    assert err.value.code == 403
