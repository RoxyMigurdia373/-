"""Entry point of the packaged Windows executable.

Double-clicking the EXE opens the native desktop window
(:mod:`vectopo_check.desktop`): pick a file, run the check and repair in the
same process, read the results. Nothing leaves the machine and no browser is
involved. ``--web`` still starts the loopback workbench for browser use, and
``--selftest`` proves that a frozen bundle can read/write GIS formats and
repair overlaps, which is what breaks first when a PyInstaller build forgets a
data file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path

from . import __version__

APP_NAME = "地类图斑拓扑检查与修复工具"
LOG_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "vectopo-check"
LOG_FILE = LOG_DIR / "launcher.log"


def log(message):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}"
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass
    if sys.stdout is not None:
        try:
            print(line, flush=True)
        except Exception:
            pass


class ServerHandle:
    """A running web workbench plus the worker thread that serves it."""

    def __init__(self, workspace, server, url):
        self.workspace = workspace
        self.server = server
        self.url = url
        self.thread = threading.Thread(target=server.serve_forever, name="vectopo-web", daemon=True)
        self.thread.start()

    @property
    def output_dir(self):
        return self.workspace.root

    def close(self):
        try:
            self.server.shutdown()
        except Exception:
            pass
        try:
            self.server.server_close()
        except Exception:
            pass
        try:
            self.workspace.close()
        except Exception:
            pass


def start_server(preferred_port=8765):
    """Bind the workbench, falling back to a free port when the default is taken."""
    from .web import WebServer, Workspace, make_handler

    workspace = Workspace()
    error = None
    for port in (preferred_port, 0):
        try:
            server = WebServer(("127.0.0.1", port), make_handler(workspace))
        except OSError as exc:  # port already in use
            error = exc
            continue
        handle = ServerHandle(workspace, server, f"http://127.0.0.1:{server.server_port}")
        log(f"本地服务已启动：{handle.url}")
        return handle
    workspace.close()
    raise RuntimeError(f"无法启动本地服务（端口被占用？）：{error}")


def _probe(url, marker, timeout=10):
    import urllib.request

    with urllib.request.urlopen(url, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
    if marker not in body and not marker.startswith("{"):
        raise AssertionError(f"{url} 返回内容缺少标记 {marker!r}")
    return body


def selftest(report_path=None):
    """Verify the bundle end to end; returns True when every probe passes."""
    import geopandas as gpd
    import pandas as pd
    import pyogrio
    import pyproj
    import shapely
    from shapely.geometry import box

    from .config import Config
    from .preset import prepare_parcels
    from .repair.overlap import find_overlaps
    from .repair.workflow import repair_layer

    checks = []

    def probe(name, action):
        try:
            checks.append({"name": name, "ok": True, "detail": str(action())})
        except Exception as exc:
            checks.append({"name": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}"})

    probe("versions", lambda: {"python": sys.version.split()[0], "frozen": bool(getattr(sys, "frozen", False)),
                               "geopandas": gpd.__version__, "shapely": shapely.__version__,
                               "pyproj": pyproj.__version__, "pyogrio": pyogrio.__version__,
                               "pandas": pd.__version__})
    probe("proj database", lambda: Path(pyproj.datadir.get_data_dir()).joinpath("proj.db").is_file())

    def geometry_math():
        merged = box(0, 0, 10, 10).union(box(5, 0, 15, 10))
        return {"union_area": merged.area, "shared": box(0, 0, 10, 10).intersection(box(5, 0, 15, 10)).area}

    probe("shapely overlay", geometry_math)

    def proj_transform():
        transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True)
        easting, northing = transformer.transform(114.06, 22.55)
        assert 100_000 < easting < 900_000, easting
        assert 2_000_000 < northing < 3_000_000, northing
        return {"easting": round(easting, 2), "northing": round(northing, 2)}

    probe("proj transform", proj_transform)

    holder = {}

    def gpkg_roundtrip():
        folder = Path(tempfile.mkdtemp(prefix="vectopo-selftest-"))
        holder["folder"] = folder
        path = folder / "probe.gpkg"
        frame = gpd.GeoDataFrame({"name": ["a", "b"]}, geometry=[box(0, 0, 4, 4), box(9, 0, 13, 4)],
                                 crs="EPSG:32650")
        frame.to_file(path, layer="probe", driver="GPKG", engine="pyogrio")
        back = gpd.read_file(path, layer="probe", engine="pyogrio")
        assert back.crs.to_epsg() == 32650, back.crs
        assert [round(value, 6) for value in back.area] == [16.0, 16.0], back.area.tolist()
        return f"{len(back)} features, {path.stat().st_size} bytes"

    probe("GPKG write/read", gpkg_roundtrip)

    def overlap_repair():
        frame = gpd.GeoDataFrame({"name": ["a", "b"]},
                                 geometry=[box(0, 0, 20, 20), box(10, 0, 30, 20)], crs="EPSG:32650")
        frame, cfg, _ = prepare_parcels(frame, Config())
        bundle = repair_layer(frame, cfg)
        verification = bundle["verification"]
        assert verification["passed"], verification
        assert find_overlaps(bundle["accepted"], cfg)[0] == []
        return {"kept": round(float(bundle["accepted"].area.sum()), 3),
                "features": len(bundle["accepted"]), "records": len(bundle["records"])}

    probe("overlap repair", overlap_repair)

    def desktop_window():
        import tkinter as tk

        from .desktop import DesktopApp

        root = tk.Tk()
        root.withdraw()
        try:
            app = DesktopApp(root)
            root.update_idletasks()
            tabs = [app.notebook.tab(tab, "text") for tab in app.notebook.tabs()]
            assert "问题明细" in tabs and "运行日志" in tabs, tabs
            return {"tabs": tabs, "scale": round(app.scale, 2)}
        finally:
            root.destroy()

    probe("desktop window", desktop_window)

    def static_page():
        page = Path(__file__).resolve().parent / "static" / "index.html"
        text = page.read_text(encoding="utf-8")
        assert "overlap_pairs" in text and "处理后复核" in text
        return f"{len(text)} chars"

    probe("web assets", static_page)

    handle = None

    def web_server():
        nonlocal handle
        handle = start_server(0)
        page = _probe(handle.url + "/", "<html")
        config = json.loads(_probe(handle.url + "/api/config", "{"))
        assert "simple_mode" in config, list(config)
        return {"url": handle.url, "page": len(page), "config_keys": sorted(config)}

    probe("web server", web_server)
    if handle is not None:
        handle.close()

    folder = holder.get("folder")
    if folder is not None:
        import shutil

        shutil.rmtree(folder, ignore_errors=True)

    report = {"ok": all(item["ok"] for item in checks), "version": __version__,
              "executable": sys.executable, "frozen": bool(getattr(sys, "frozen", False)),
              "checks": checks}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if report_path:
        Path(report_path).write_text(text, encoding="utf-8")
    log("自检" + ("通过" if report["ok"] else "失败") + f"：{report_path or '未写出报告'}")
    for item in checks:
        log(f"  [{'OK' if item['ok'] else 'FAIL'}] {item['name']}: {item['detail']}")
    return report["ok"]


def enable_dpi_awareness():
    """Render crisply on scaled displays; the PyInstaller manifest is DPI-unaware."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
    except Exception:  # pragma: no cover - restricted runtime
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # legacy system DPI aware
    except Exception:
        pass


def build_window(root, port=8765, open_browser=True, autostart=True):
    """Create the launcher widgets on ``root``; returns handles used by tests."""
    import tkinter as tk
    from tkinter import messagebox, ttk

    try:
        scale = max(1.0, min(3.0, float(root.winfo_fpixels("1i")) / 96.0))
    except Exception:
        scale = 1.0

    def px(value):
        return int(round(value * scale))

    root.title(f"{APP_NAME} v{__version__}")
    root.geometry(f"{px(640)}x{px(470)}")
    root.minsize(px(560), px(430))

    state = {"handle": None, "opened": False}
    ui = {"state": state, "scale": scale, "url": None, "entry": None, "buttons": {}, "log": None}

    header = ttk.Frame(root, padding=(px(18), px(16), px(18), px(6)))
    header.pack(fill="x")
    ttk.Label(header, text=APP_NAME, font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
    ttk.Label(header, text="完全离线运行 · 支持 GPKG / GeoJSON / SHP(ZIP) · 数据不出本机",
              foreground="#57606a").pack(anchor="w", pady=(px(4), 0))

    address = ttk.Frame(root, padding=(px(18), px(10), px(18), px(4)))
    address.pack(fill="x")
    ttk.Label(address, text="服务地址").pack(side="left")
    url_var = tk.StringVar(value="正在启动本地服务…")
    entry = ttk.Entry(address, textvariable=url_var, state="readonly")
    entry.pack(side="left", fill="x", expand=True, padx=px(10))

    actions = ttk.Frame(root, padding=(px(18), px(10), px(18), px(6)))
    actions.pack(fill="x")
    buttons = {}
    for label, side, padx in (("打开检查界面", "left", 0), ("复制地址", "left", px(8)), ("打开成果目录", "left", 0)):
        button = ttk.Button(actions, text=label, state="disabled")
        button.pack(side=side, padx=padx)
        buttons[label] = button
    quit_button = ttk.Button(actions, text="退出")
    quit_button.pack(side="right")

    body = ttk.Frame(root, padding=(px(18), px(4), px(18), px(10)))
    body.pack(fill="both", expand=True)
    log_view = tk.Text(body, height=8, wrap="word", state="disabled", font=("Consolas", 9))
    scroll = ttk.Scrollbar(body, orient="vertical", command=log_view.yview)
    log_view.configure(yscrollcommand=scroll.set)
    log_view.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")

    footer = ttk.Label(root, padding=(px(18), 0, px(18), px(12)), foreground="#57606a",
                       text="成果保存在本机临时目录，关闭窗口后清理。处理记录会写入 "
                            f"{LOG_FILE}")
    footer.pack(fill="x")

    def append(message):
        log(message)
        log_view.configure(state="normal")
        log_view.insert("end", f"{datetime.now():%H:%M:%S}  {message}\n")
        log_view.see("end")
        log_view.configure(state="disabled")

    def open_page():
        handle = state["handle"]
        if handle is None:
            return
        webbrowser.open(handle.url)
        state["opened"] = True

    def copy_address():
        handle = state["handle"]
        if handle is None:
            return
        root.clipboard_clear()
        root.clipboard_append(handle.url)
        append("服务地址已复制到剪贴板")

    def open_folder():
        handle = state["handle"]
        if handle is None:
            return
        target = handle.output_dir
        try:
            os.startfile(target)  # noqa: S606 - Windows only, opens Explorer
        except Exception as exc:
            append(f"无法打开目录 {target}：{exc}")

    def shutdown():
        handle = state["handle"]
        state["handle"] = None
        if handle is not None:
            append("正在关闭本地服务…")
            handle.close()
        root.destroy()

    def attach(handle):
        """Called on the Tk thread once the service is listening."""
        state["handle"] = handle
        url_var.set(handle.url)
        for button in buttons.values():
            button.configure(state="normal")
        append(f"服务已就绪：{handle.url}")
        append("在浏览器中上传矢量数据即可开始检查；勾选“自动修复”可生成处理成果。")
        if open_browser and not state["opened"]:
            open_page()

    def failed(exc):
        append(f"启动失败：{type(exc).__name__}: {exc}")
        append(traceback.format_exc(limit=3))
        messagebox.showerror(APP_NAME, f"本地服务启动失败：\n{exc}\n\n详细日志：{LOG_FILE}")

    def boot():
        try:
            handle = start_server(port)
        except Exception as exc:
            root.after(0, lambda error=exc: failed(error))
            return
        root.after(0, lambda running=handle: attach(running))

    buttons["打开检查界面"].configure(command=open_page)
    buttons["复制地址"].configure(command=copy_address)
    buttons["打开成果目录"].configure(command=open_folder)
    quit_button.configure(command=shutdown)
    root.protocol("WM_DELETE_WINDOW", shutdown)
    if autostart:
        root.after(120, lambda: threading.Thread(target=boot, name="vectopo-boot", daemon=True).start())
    append(f"启动中…（日志：{LOG_FILE}）")

    ui.update({"url": url_var, "entry": entry, "buttons": buttons, "log": log_view,
               "append": append, "attach": attach, "boot": boot, "shutdown": shutdown})
    return ui


def run_gui(port=8765, open_browser=True):
    import tkinter as tk

    enable_dpi_awareness()
    root = tk.Tk()
    build_window(root, port=port, open_browser=open_browser)
    root.mainloop()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="vectopo-check", description=f"{APP_NAME} v{__version__}")
    parser.add_argument("--input", metavar="PATH", default=None, help="启动后直接打开这个矢量文件")
    parser.add_argument("--web", action="store_true", help="改用浏览器界面（本机回环服务），而不是桌面窗口")
    parser.add_argument("--port", type=int, default=8765, help="浏览器界面的端口，默认 8765（被占用时自动改用空闲端口）")
    parser.add_argument("--no-browser", action="store_true", help="浏览器界面启动后不自动打开浏览器")
    parser.add_argument("--serve", action="store_true", help="只启动本机回环服务，不显示窗口（用于脚本或调试）")
    parser.add_argument("--selftest", nargs="?", const="selftest.json", default=None,
                        metavar="REPORT", help="运行打包自检并写出 JSON 报告")
    args = parser.parse_args(argv)

    if args.selftest is not None:
        return 0 if selftest(args.selftest) else 1
    if args.serve:
        handle = start_server(args.port)
        if not args.no_browser:
            webbrowser.open(handle.url)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            handle.close()
        return 0
    if args.web:
        run_gui(args.port, not args.no_browser)
        return 0
    from .desktop import launch

    launch(initial_path=args.input)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        log("未处理异常：\n" + traceback.format_exc())
        if sys.stderr is not None:
            traceback.print_exc()
        raise
