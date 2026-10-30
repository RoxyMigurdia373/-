"""Session-scoped RPC adapter for the shared HTML workbench."""
import base64
from pathlib import Path

import yaml

from .config import Config
from .cloud_transfer import upload_request, CHUNK_SIZE
from .export import write_shp_zip
from .web import REPORT_FILES, Workspace


class CloudWorkspace(Workspace):
    def __del__(self):
        self.close()


def dispatch(workspace, request):
    path, data = request["path"], request.get("data") or {}
    if path == "/api/config":
        config = yaml.safe_load(data.get("yaml", "")) or {} if data else {}
        return Config.model_validate(config).model_dump(mode="json")
    if path.startswith("/api/upload/"):
        return upload_request(workspace, path.rsplit("/", 1)[-1], data)
    if path == "/api/upload":
        files = data.get("files", [])
        if sum(len(f.get("data", "")) for f in files) > 240 * 1024 * 1024:
            raise ValueError("文件总大小超过 180 MB")
        return workspace.upload(files)
    if path == "/api/check":
        return workspace.start(data)
    parts = path.strip("/").split("/")
    if len(parts) not in {3, 4} or parts[:2] != ["api", "jobs"]:
        raise ValueError("未找到接口")
    job = parts[2]
    with workspace.lock:
        result = workspace.jobs.get(job)
    if result is None:
        raise ValueError("任务不存在，请重新上传")
    if len(parts) == 3:
        return result
    name = parts[3]
    if result["status"] != "done":
        raise ValueError("任务尚未完成")
    if name not in REPORT_FILES | {"processed_shp.zip", "checked_features_shp.zip"}:
        raise ValueError("不支持的下载文件")
    processed = name.startswith("repair_") or name == "processed_shp.zip"
    if processed and (not result.get("repair_enabled") or result.get("repair_error")):
        raise ValueError("修复成果不可用")
    if name.startswith("checked_features") and result.get("vector_export_error"):
        raise ValueError("矢量成果不可用")
    folder = workspace.root / job
    target = folder / name
    if name in {"processed_shp.zip", "checked_features_shp.zip"}:
        with workspace.export_lock:
            if not target.exists():
                write_shp_zip(folder / ("repair_staging.gpkg" if processed else "checked_features.gpkg"),
                              "processed" if processed else "checked_features", target)
    if data.get("metadata"):
        return {"name": name, "size": target.stat().st_size, "chunk_size": CHUNK_SIZE}
    if "offset" in data:
        offset = data["offset"]
        if type(offset) is not int or not 0 <= offset <= target.stat().st_size:
            raise ValueError("下载偏移无效")
        with target.open("rb") as stream:
            stream.seek(offset)
            chunk = stream.read(CHUNK_SIZE)
        return {"name": name, "base64": base64.b64encode(chunk).decode()}
    return {"name": name, "base64": base64.b64encode(target.read_bytes()).decode()}


def component_html():
    static = Path(__file__).parent / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    bridge = (static / "streamlit_bridge.js").read_text(encoding="utf-8")
    return html.replace("<script>", "<script>" + bridge + "</script><script>", 1).replace(
        "● 本地运行 · 数据留在本机", "● 云端运行 · 请及时下载成果").replace(
        "总文件大小不超过 180 MB", "总文件大小不超过 180 MB · 文件上传至云端处理")
