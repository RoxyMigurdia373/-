"""Loopback-only UI, using the standard library and the existing check engine."""
import base64
import binascii
import json
import hashlib
import shutil
import tempfile
import threading
import uuid
import webbrowser
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZipFile

import geopandas as gpd
import yaml
from shapely.geometry import mapping

from .checks.base import json_finite, safe_geometry
from .config import Config
from .engine import check_layer, run_repair
from .io import read_layer
from .report import write_reports
from .export import write_vector_export, write_report_bundle, write_shp_zip
from .preset import prepare_parcels
from .progress import progress_percent

MAX_BODY = 256 * 1024 * 1024
PREVIEW_LIMIT = 2000
REPORT_FILES = {"report.json", "issues.geojson", "report.csv", "summary.txt", "checked_features.gpkg", "check_reports.zip", "repair_report.json", "repair_staging.gpkg"}
UPLOAD_SUFFIXES = {".gpkg", ".geojson", ".json", ".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".zip"}


def unpack_shapefile(archive_path, folder):
    """Select one SHP bundle and normalize names without trusting ZIP paths."""
    with ZipFile(archive_path) as archive:
        entries = [i for i in archive.infolist() if not i.is_dir() and not i.filename.startswith('__MACOSX/')]
        if sum(i.file_size for i in entries) > 512 * 1024 * 1024:
            raise ValueError("ZIP 解压后超过 512 MB，请拆分图层")
        mains = [i for i in entries if i.filename.lower().endswith('.shp')]
        if len(mains) != 1:
            raise ValueError("ZIP 内必须包含且仅包含一个 SHP 图层")
        main = mains[0]
        stem = main.filename[:-4].lower()
        companions = {}
        for item in entries:
            name = item.filename.lower()
            suffix = Path(name).suffix
            if name[:-len(suffix)] == stem and suffix in UPLOAD_SUFFIXES - {'.zip'}:
                if suffix in companions:
                    raise ValueError("ZIP 内同名配套文件重复")
                companions[suffix] = item
        if not {'.shp', '.shx', '.dbf'}.issubset(companions):
            raise ValueError("ZIP 中缺少同名 .shp/.shx/.dbf 文件")
        target = folder / 'extracted'
        target.mkdir()
        for suffix, item in companions.items():
            # A fixed basename avoids ZIP filename encoding differences on Windows.
            with archive.open(item) as source, (target / ('dataset' + suffix)).open('wb') as dest:
                shutil.copyfileobj(source, dest)
        return target / 'dataset.shp'


class Workspace:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="vectopo-web-"))
        self.datasets = {}
        self.jobs = {}
        self.request_jobs = {}
        self.lock = threading.Lock()
        self.export_lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=1)

    def close(self):
        self.pool.shutdown(wait=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def upload(self, files):
        if not isinstance(files, list) or not files:
            raise ValueError("请选择矢量文件；SHP 请同时选择同名配套文件")
        token = uuid.uuid4().hex
        folder = self.root / token
        folder.mkdir()
        try:
            names = set()
            paths = []
            for item in files:
                name = item["name"]
                if not isinstance(name, str) or not name or any(c in name for c in '/\\:') or name in {".", ".."}:
                    raise ValueError("文件名不合法")
                path = folder / name
                if name.lower().endswith('.shp.xml'):
                    continue
                if path.suffix.lower() not in UPLOAD_SUFFIXES or name.lower() in names:
                    raise ValueError("文件类型不支持或文件名重复")
                names.add(name.lower())
                path.write_bytes(base64.b64decode(item["data"], validate=True))
                paths.append(path)
            return self.register_upload(token, folder, paths)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def register_upload(self, token, folder, paths):
        """Register already-written files after normal or chunked upload."""
        primary = [p for p in paths if p.suffix.lower() in {".gpkg", ".geojson", ".json", ".shp", ".zip"}]
        if len(primary) != 1:
            raise ValueError("每次请选择一个主文件；SHP 配套文件可一起选择")
        path = primary[0]
        display_name = path.name
        if path.suffix.lower() == '.zip':
            path = unpack_shapefile(path, folder)
        elif path.suffix.lower() == ".shp" and not all(path.with_suffix(ext).name.lower() in {p.name.lower() for p in paths} for ext in (".shx", ".dbf")):
            raise ValueError("SHP 需要同名 .shx 和 .dbf 文件，建议同时提供 .prj")
        layers = [str(row["name"]) for _, row in gpd.list_layers(path).iterrows()]
        with self.lock:
            self.datasets[token] = path
        return {"dataset": token, "name": display_name, "layers": layers}

    def start(self, request):
        cfg = Config.model_validate(request.get("config", {}))
        cfg.simple_mode = bool(request.get("simple_mode", cfg.simple_mode))
        repair = request.get("repair", False)
        if not isinstance(repair, bool):
            raise ValueError("repair 必须为布尔值")
        key = hashlib.sha256(json.dumps({"dataset": request.get("dataset"), "layer": request.get("layer"),
                                       "config": cfg.model_dump(), "repair": repair}, sort_keys=True).encode()).hexdigest()
        with self.lock:
            path = self.datasets.get(request.get("dataset"))
            if path is None:
                raise ValueError("文件会话不存在，请重新上传")
            previous = self.request_jobs.get(key)
            existing = self.jobs.get(previous, {})
            if existing.get("status") in {"queued", "running"} or (
                existing.get("status") == "done" and not existing.get("repair_error") and not existing.get("vector_export_error")
            ):
                return {"job": previous, "reused": True}
            if any(j["status"] in {"queued", "running"} for j in self.jobs.values()):
                raise ValueError("已有检查正在运行，请等待完成")
            job = uuid.uuid4().hex
            self.jobs[job] = {"status": "queued", "progress": 0}
            self.request_jobs[key] = job
        self.pool.submit(self._run, job, path, request.get("layer") or None, cfg, repair)
        return {"job": job}

    def _run(self, job, path, layer, cfg, repair=False):
        def progress(message):
            with self.lock:
                previous = self.jobs.get(job, {}).get("progress", 0)
                self.jobs[job] = {"status": "running", "message": message, "progress": max(previous, progress_percent(message))}
        with self.lock:
            self.jobs[job] = {"status": "running"}
        try:
            progress("正在读取矢量数据…")
            gdf = read_layer(path, layer)
            preset = None
            if cfg.simple_mode:
                progress("正在自动选择米制坐标系，面积阈值固定为 0.1 亩…")
                gdf, cfg, preset = prepare_parcels(gdf, cfg)
            report = check_layer(gdf, cfg, input_path=path.name, layer=layer, progress=progress)
            out = self.root / job
            progress("检查完成，正在写入报告和压缩包…")
            write_reports(report, out)
            write_report_bundle(out)
            export_error = None
            fields = None
            try:
                progress("正在导出带问题字段的矢量副本…")
                fields = write_vector_export(gdf, report, cfg, out)
            except Exception as exc:
                export_error = str(exc)
            repair_summary, repair_error = None, None
            if repair:
                try:
                    progress("正在切开岛洞并合并小于0.1亩图斑，逐项复核邻接关系…" if cfg.simple_mode else "正在生成修复候选并逐事务全图层复检，耗时可能较长…")
                    repair_summary = run_repair(gdf, cfg, out, path, progress=progress)["summary"]
                except Exception as exc:
                    repair_error = str(exc)
            stride = max(1, (len(gdf) + PREVIEW_LIMIT - 1) // PREVIEW_LIMIT)
            features = [{"type": "Feature", "geometry": json_finite(mapping(g)), "properties": {}}
                        for g in gdf.geometry.iloc[::stride] if safe_geometry(g) is not None]
            browser_report = report.model_dump(mode="json", exclude={"issues"})
            browser_report["issues"] = [i.model_dump(mode="json") for i in report.issues[:PREVIEW_LIMIT]]
            result = {"status": "done", "progress": 100, "report": browser_report,
                      "severity_counts": dict(Counter(i.severity for i in report.issues)),
                      "preview_limited": len(gdf) > PREVIEW_LIMIT or len(report.issues) > PREVIEW_LIMIT,
                      "preview_limit": PREVIEW_LIMIT,
                      "vector_fields": fields, "vector_export_error": export_error,
                      "repair_enabled": repair, "repair_summary": repair_summary, "repair_error": repair_error,
                      "preset": preset,
                      "coverage": {"type": "FeatureCollection", "features": features}}
        except Exception as exc:
            result = {"status": "error", "error": str(exc)}
        with self.lock:
            self.jobs[job] = result


def make_handler(workspace):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def reply(self, status, data, content_type="application/json; charset=utf-8", attachment=None):
            payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode() if isinstance(data, (dict, list)) else data
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            if attachment:
                self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
            self.end_headers()
            self.wfile.write(payload)

        def local_request(self):
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            origin = self.headers.get("Origin")
            return self.headers.get("Host") in allowed and (not origin or origin in {f"http://{host}" for host in allowed})

        def do_GET(self):
            if not self.local_request():
                return self.reply(403, {"error": "仅允许本机同源访问"})
            path = urlsplit(self.path).path
            if path == "/":
                return self.reply(200, (Path(__file__).parent / "static" / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/api/config":
                return self.reply(200, Config().model_dump(mode="json"))
            if path.startswith("/api/jobs/"):
                parts = path.strip("/").split("/")
                if len(parts) not in {3, 4}:
                    return self.reply(404, {"error": "未找到资源"})
                with workspace.lock:
                    result = workspace.jobs.get(parts[2])
                if result is None:
                    return self.reply(404, {"error": "检查任务不存在"})
                if len(parts) == 3:
                    return self.reply(200, result)
                if len(parts) == 4 and parts[3] in {"checked_features_shp.zip", "processed_shp.zip"} and result["status"] == "done":
                    processed = parts[3] == "processed_shp.zip"
                    if (processed and (not result.get("repair_enabled") or result.get("repair_error"))) or (not processed and result.get("vector_export_error")):
                        return self.reply(400, {"error": "相应矢量成果不可用，请重新运行检查"})
                    folder = workspace.root / parts[2]
                    target = folder / parts[3]
                    try:
                        with workspace.export_lock:
                            if not target.exists():
                                write_shp_zip(folder / ("repair_staging.gpkg" if processed else "checked_features.gpkg"),
                                              "processed" if processed else "checked_features", target)
                        return self.reply(200, target.read_bytes(), "application/zip", parts[3])
                    except Exception as exc:
                        return self.reply(400, {"error": f"SHP 导出失败（GPKG 仍可下载）：{exc}"})
                if len(parts) == 4 and parts[3] in REPORT_FILES and result["status"] == "done":
                    file = workspace.root / parts[2] / parts[3]
                    if not file.is_file() or (parts[3].startswith("repair_") and result.get("repair_error")) or (parts[3] == "checked_features.gpkg" and result.get("vector_export_error")):
                        return self.reply(404, {"error": result.get("vector_export_error") or "导出文件不存在"})
                    return self.reply(200, file.read_bytes(), "application/octet-stream", parts[3])
            self.reply(404, {"error": "未找到资源"})

        def do_POST(self):
            if not self.local_request():
                return self.reply(403, {"error": "仅允许本机同源访问"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > MAX_BODY:
                    return self.reply(413, {"error": "上传请求过大（含编码最大 256 MB）或为空"})
                request = json.loads(self.rfile.read(size))
                if not isinstance(request, dict):
                    raise ValueError("请求必须为对象")
                path = urlsplit(self.path).path
                if path == "/api/upload":
                    return self.reply(200, workspace.upload(request.get("files")))
                if path == "/api/check":
                    return self.reply(202, workspace.start(request))
                if path == "/api/config":
                    config = yaml.safe_load(request.get("yaml", "")) or {}
                    return self.reply(200, Config.model_validate(config).model_dump(mode="json"))
                self.reply(404, {"error": "未找到接口"})
            except (ValueError, KeyError, TypeError, binascii.Error, yaml.YAMLError) as exc:
                self.reply(400, {"error": str(exc)})
            except Exception as exc:
                self.reply(400, {"error": f"文件读取失败：{exc}"})

    return Handler


def serve(port=8765, open_browser=True):
    workspace = Workspace()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(workspace))
        url = f"http://127.0.0.1:{server.server_port}"
        print(f"vectopo-check 网页版：{url}\n按 Ctrl+C 停止。", flush=True)
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        workspace.close()
