"""Native desktop window: pick a file, get reports and repaired data back.

The window calls :func:`vectopo_check.pipeline.run_pipeline` directly, in one
worker thread. Nothing is uploaded, nothing is copied through base64, and no
browser is involved, so a large layer only costs the actual check time. Tk is
touched exclusively from ``after`` callbacks fed by a queue, which keeps the
window responsive (no greyed-out shell, no busy cursor) while it runs.
"""

from __future__ import annotations

import os
import queue
import tempfile
import threading
import time
import traceback
from pathlib import Path

from . import __version__
from .config import Config
from .export import write_shp_zip
from .io import list_layer_names, unpack_shapefile
from .launcher import APP_NAME, LOG_FILE, enable_dpi_awareness, log
from .pipeline import Cancelled, run_pipeline
from .progress import progress_percent

FILETYPES = [
    ("矢量数据", "*.gpkg *.geojson *.json *.shp *.zip"),
    ("GeoPackage", "*.gpkg"),
    ("GeoJSON", "*.geojson *.json"),
    ("Shapefile", "*.shp"),
    ("SHP 压缩包", "*.zip"),
    ("全部文件", "*.*"),
]
ISSUE_ROWS = 1500
SEVERITY_TAGS = {"ERROR": ("#b42318", "#fef3f2"), "WARN": ("#b54708", "#fffaeb"), "INFO": ("#175cd3", "#eff8ff")}
# Hole strategies, in the order they appear in the window. The first one is the
# default: a 0.1 mm slit keeps one parcel and loses well under a square metre.
HOLE_CHOICES = (
    ("slit", "0.1 毫米细缝"),
    ("cut_open", "1 厘米开缝"),
    ("split_open", "零宽切分"),
    ("fill", "填平"),
    ("legacy", "不处理"),
)
HOLE_HINTS = {
    "slit": "0.1 毫米细缝：切口损失约 0.0006 m²/条（≈0.000001 亩），仍是 1 个图斑。",
    "cut_open": "1 厘米开缝：损失约 0.01 m² × 缝长，通常不足 1 m²，仍是 1 个图斑。",
    "split_open": "零宽切分：沿穿过洞的切线把图斑拆成几块，面积严格不变、都不带洞，图斑数会变多。",
    "fill": "填平：洞并进图斑，单要素、无裂缝，但图斑面积会变大（总量增加）。",
    "legacy": "不处理：洞保持原样，只在配置满足自动填充条件时才填。",
}


class DesktopApp:
    """The whole workbench in one window."""

    def __init__(self, root, initial_path=None, output_dir=None):
        self.root = root
        self.queue = queue.Queue()
        self.cancel = threading.Event()
        self.worker = None
        self.payload = None
        self.run_dir = None
        self.source = None
        self.started_at = 0.0
        self.temp_dirs = []
        self._closing = False
        self._percent = 0.0
        self._finished = True
        self._custom_output = bool(output_dir)
        self._build(output_dir)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<F5>", lambda event: self.start())
        self.root.bind("<Control-o>", lambda event: self.pick_file())
        self.root.after(120, self._pump)
        if initial_path:
            self.root.after(40, lambda: self.set_path(initial_path))

    # ------------------------------------------------------------------ layout
    def _build(self, output_dir):
        import tkinter as tk
        from tkinter import ttk

        root = self.root
        try:
            scale = max(1.0, min(3.0, float(root.winfo_fpixels("1i")) / 96.0))
        except Exception:
            scale = 1.0
        self.scale = scale

        def px(value):
            return int(round(value * scale))

        root.title(f"{APP_NAME} v{__version__}")
        root.geometry(f"{px(1000)}x{px(740)}")
        root.minsize(px(860), px(620))
        self.px = px
        style = ttk.Style(root)
        for theme in ("vista", "winnative", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        style.configure("Run.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Treeview", rowheight=px(22))

        header = ttk.Frame(root, padding=(px(16), px(12), px(16), px(2)))
        header.pack(fill="x")
        ttk.Label(header, text=APP_NAME, font=("Microsoft YaHei UI", 14, "bold")).pack(side="left")
        ttk.Label(header, text=f"v{__version__} · 完全离线 · 数据不出本机",
                  foreground="#57606a").pack(side="right", pady=(px(4), 0))

        self.path_var = tk.StringVar()
        self.layer_var = tk.StringVar()
        self.out_var = tk.StringVar(value=str(output_dir) if output_dir else "")
        self.simple_var = tk.BooleanVar(value=True)
        self.repair_var = tk.BooleanVar(value=True)
        self.hole_var = tk.StringVar(value=HOLE_CHOICES[0][0])
        self.status_var = tk.StringVar(value="等待选择数据")
        self.message_var = tk.StringVar(value="就绪")
        self.percent_var = tk.StringVar(value="0%")
        self.progress_var = tk.DoubleVar(value=0.0)

        data = ttk.LabelFrame(root, text="数据", padding=(px(12), px(8), px(12), px(10)))
        data.pack(fill="x", padx=px(16), pady=(px(8), 0))
        data.columnconfigure(1, weight=1)
        ttk.Label(data, text="数据文件").grid(row=0, column=0, sticky="w")
        ttk.Entry(data, textvariable=self.path_var, state="readonly").grid(
            row=0, column=1, columnspan=2, sticky="ew", padx=px(8))
        ttk.Button(data, text="选择文件…", command=self.pick_file).grid(row=0, column=3)
        ttk.Label(data, text="图层").grid(row=1, column=0, sticky="w", pady=(px(6), 0))
        self.layer_box = ttk.Combobox(data, textvariable=self.layer_var, state="disabled", width=28, values=[])
        self.layer_box.grid(row=1, column=1, sticky="w", padx=px(8), pady=(px(6), 0))
        self.layer_hint = ttk.Label(data, text="支持 GPKG / GeoJSON / SHP / SHP 压缩包", foreground="#57606a")
        self.layer_hint.grid(row=1, column=2, columnspan=2, sticky="w", pady=(px(6), 0))

        options = ttk.LabelFrame(root, text="处理选项", padding=(px(12), px(8), px(12), px(10)))
        options.pack(fill="x", padx=px(16), pady=(px(8), 0))
        options.columnconfigure(4, weight=1)
        ttk.Checkbutton(options, text="简易模式（面积统一按 0.1 亩判断）",
                        variable=self.simple_var).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(options, text="自动修复（重叠面 / 小面积 / 岛洞 / 多部件）",
                        variable=self.repair_var).grid(row=0, column=1, columnspan=2, sticky="w", padx=px(18))
        ttk.Label(options, text="成果目录").grid(row=1, column=0, sticky="w", pady=(px(8), 0))
        ttk.Entry(options, textvariable=self.out_var).grid(row=1, column=1, columnspan=3, sticky="ew",
                                                          padx=px(8), pady=(px(8), 0))
        ttk.Button(options, text="选择目录…", command=self.pick_output).grid(row=1, column=4, sticky="w",
                                                                          pady=(px(8), 0))
        self.mode_hint = ttk.Label(options, text="", foreground="#57606a")
        self.mode_hint.grid(row=2, column=0, columnspan=5, sticky="w", pady=(px(6), 0))
        hole_row = ttk.Frame(options)
        hole_row.grid(row=3, column=0, columnspan=5, sticky="w", pady=(px(6), 0))
        ttk.Label(hole_row, text="岛洞处理").pack(side="left")
        self.hole_buttons = {}
        for value, label in HOLE_CHOICES:
            button = ttk.Radiobutton(hole_row, text=label, value=value, variable=self.hole_var)
            button.pack(side="left", padx=(px(8), 0))
            self.hole_buttons[value] = button
        self.hole_hint = ttk.Label(options, text="", foreground="#57606a")
        self.hole_hint.grid(row=4, column=0, columnspan=5, sticky="w", pady=(px(4), 0))
        self.simple_var.trace_add("write", lambda *_: self._sync_mode())
        self.hole_var.trace_add("write", lambda *_: self._sync_mode())
        self._sync_mode()

        bar = ttk.Frame(root, padding=(px(16), px(8), px(16), 0))
        bar.pack(fill="x")
        self.start_button = ttk.Button(bar, text="开始处理（F5）", style="Run.TButton", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(bar, text="停止", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=(px(8), 0))
        self.folder_button = ttk.Button(bar, text="打开成果目录", command=self.open_folder, state="disabled")
        self.folder_button.pack(side="left", padx=(px(18), 0))
        self.export_buttons = {}
        for key, label in (("checked", "导出检查 SHP"), ("repaired", "导出修复 SHP")):
            button = ttk.Button(bar, text=label, command=lambda kind=key: self.export_shp(kind), state="disabled")
            button.pack(side="left", padx=(px(8), 0))
            self.export_buttons[key] = button
        ttk.Label(bar, textvariable=self.status_var, foreground="#57606a").pack(side="right")

        progress = ttk.Frame(root, padding=(px(16), px(8), px(16), 0))
        progress.pack(fill="x")
        self.progress = ttk.Progressbar(progress, mode="determinate", maximum=100, variable=self.progress_var)
        self.progress.pack(fill="x")
        line = ttk.Frame(progress)
        line.pack(fill="x", pady=(px(4), 0))
        ttk.Label(line, textvariable=self.message_var, foreground="#57606a").pack(side="left")
        ttk.Label(line, textvariable=self.percent_var, foreground="#57606a").pack(side="right")

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=px(16), pady=(px(8), px(6)))
        self.summary_tree = self._tree_tab("处理汇总", ("项目", "数值", "说明"), (px(220), px(120), px(520)))
        self.issue_tree = self._tree_tab("问题明细", ("类型", "级别", "要素", "关联", "说明"),
                                         (px(150), px(80), px(120), px(120), px(470)))
        log_frame = ttk.Frame(self.notebook, padding=(px(4), px(4), px(4), px(4)))
        self.notebook.add(log_frame, text="运行日志")
        self.log_view = tk.Text(log_frame, height=10, wrap="word", state="disabled", font=("Consolas", 9),
                                relief="flat", background="#fbfbfb")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_view.yview)
        self.log_view.configure(yscrollcommand=scroll.set)
        self.log_view.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        footer = ttk.Frame(root, padding=(px(16), 0, px(16), px(8)))
        footer.pack(fill="x")
        ttk.Label(footer, text=f"成果：report.json / issues.geojson / report.csv / repair_staging.gpkg；日志：{LOG_FILE}",
                  foreground="#57606a").pack(side="left")

    def _tree_tab(self, title, columns, widths):
        from tkinter import ttk

        frame = ttk.Frame(self.notebook, padding=(self.px(4),) * 4)
        self.notebook.add(frame, text=title)
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        for column, width in zip(columns, widths):
            tree.heading(column, text=column)
            tree.column(column, width=width, anchor="w", stretch=column in {columns[-1]})
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for tag, (foreground, background) in SEVERITY_TAGS.items():
            tree.tag_configure(tag, foreground=foreground, background=background)
        return tree

    # --------------------------------------------------------------- behaviour
    def _sync_mode(self):
        if self.simple_var.get():
            self.mode_hint.configure(
                text="简易模式：自动转到米制投影，面积阈值固定 0.1 亩（66.67 m²），共边不算重叠。")
        else:
            self.mode_hint.configure(
                text="高级模式：沿用配置默认阈值（最小面积 1 m²、重叠 0.01 m²），结果依赖输入坐标系。")
        self.hole_hint.configure(text=HOLE_HINTS.get(self.hole_var.get(), ""))

    def pick_file(self):
        from tkinter import filedialog

        path = filedialog.askopenfilename(title="选择矢量数据", filetypes=FILETYPES)
        if path:
            self.set_path(path)

    def pick_output(self):
        from tkinter import filedialog

        folder = filedialog.askdirectory(title="选择成果目录")
        if folder:
            self.out_var.set(folder)
            self._custom_output = True

    def set_path(self, path):
        """Accept a dataset path, unpacking SHP bundles and reading layer names."""
        from tkinter import messagebox

        raw = Path(path)
        if not raw.exists():
            self._log(f"文件不存在：{raw}")
            return
        source = raw
        if raw.suffix.lower() == ".zip":
            try:
                folder = Path(tempfile.mkdtemp(prefix="vectopo-zip-"))
                self.temp_dirs.append(folder)
                source = unpack_shapefile(raw, folder)
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"{raw.name} 不是可用的 SHP 压缩包：\n{exc}")
                return
        self.source = source
        self.path_var.set(str(raw))
        if not self._custom_output:
            self.out_var.set(str(raw.with_name(f"{raw.stem}_成果")))
        self.refresh_layers()
        self._log(f"已选择：{raw}")
        self.status_var.set("已选择数据，点“开始处理”")

    def refresh_layers(self):
        if self.source is None:
            return
        layers = list_layer_names(self.source)
        self.layer_box.configure(values=layers)
        if not layers:
            self.layer_var.set("")
            self.layer_box.configure(state="disabled")
            self.layer_hint.configure(text="该格式为单图层，无需指定图层")
        elif len(layers) == 1:
            self.layer_var.set(layers[0])
            self.layer_box.configure(state="readonly")
            self.layer_hint.configure(text="共 1 个图层")
        else:
            if self.layer_var.get() not in layers:
                self.layer_var.set(layers[0])
            self.layer_box.configure(state="readonly")
            self.layer_hint.configure(text=f"共 {len(layers)} 个图层，请选择要检查的图层")

    def _config(self, output_dir):
        cfg = Config()
        cfg.simple_mode = bool(self.simple_var.get())
        cfg.repair.hole_action = self.hole_var.get()
        cfg.output_dir = str(output_dir)
        return cfg

    def start(self):
        from tkinter import messagebox

        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo(APP_NAME, "已有任务在运行，请等待完成或先点“停止”。")
            return
        if self.source is None:
            messagebox.showwarning(APP_NAME, "请先选择要处理的矢量数据。")
            return
        text = self.out_var.get().strip()
        if not text:
            messagebox.showwarning(APP_NAME, "请选择成果目录。")
            return
        out = Path(text)
        try:
            out.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"无法创建成果目录：\n{exc}")
            return
        self._reset_results()
        cfg = self._config(out)
        layer = self.layer_var.get() or None
        repair = bool(self.repair_var.get())
        self.started_at = time.perf_counter()
        self.cancel.clear()
        self._set_running(True)
        self._log(f"开始处理：{self.source}")
        self._log(f"参数：图层 {layer or '自动'}，简易模式 {'开' if cfg.simple_mode else '关'}，"
                  f"自动修复 {'开' if repair else '关'}，岛洞 {dict(HOLE_CHOICES).get(cfg.repair.hole_action, '?')}，"
                  f"成果目录 {out}")
        self.worker = threading.Thread(target=self._work,
                                       args=(self.source, layer, cfg, out, repair, cfg.repair.hole_action),
                                       name="vectopo-run", daemon=True)
        self.worker.start()

    def _work(self, source, layer, cfg, out, repair, hole_action):
        def progress(message):
            self.queue.put(("progress", message))
        try:
            payload = run_pipeline(source, layer=layer, cfg=cfg, output_dir=out, repair=repair,
                                   progress=progress, cancel=self.cancel.is_set,
                                   hole_action=hole_action)
        except Cancelled:
            self.queue.put(("cancelled", None))
        except Exception:
            self.queue.put(("error", traceback.format_exc()))
        else:
            self.queue.put(("done", payload))

    def _direct_progress(self, message):
        """Progress sink for synchronous runs (tests, selftest, --input boot)."""
        self.message_var.set(message)
        percent = max(self._percent, max(0.0, min(100.0, float(progress_percent(message)))))
        self._percent = percent
        self.progress_var.set(percent)
        self.percent_var.set(f"{percent:.0f}%")
        self.root.update_idletasks()

    def run_blocking(self, path=None, layer=None, output_dir=None, repair=None, cancel=None):
        """Run one job on the calling thread; used by tests and the selftest."""
        source = Path(path) if path is not None else self.source
        out = Path(output_dir) if output_dir is not None else Path(self.out_var.get() or "report")
        cfg = self._config(out)
        payload = run_pipeline(source, layer=layer if layer is not None else (self.layer_var.get() or None),
                               cfg=cfg, output_dir=out, progress=self._direct_progress,
                               repair=bool(self.repair_var.get()) if repair is None else repair,
                               cancel=cancel, hole_action=cfg.repair.hole_action)
        self.started_at = self.started_at or time.perf_counter()
        self._on_done(payload)
        self.root.update_idletasks()
        return payload

    def stop(self):
        if self.worker is None or not self.worker.is_alive():
            return
        self.cancel.set()
        self.status_var.set("正在停止…")
        self.message_var.set("正在停止，等当前步骤结束…")
        self._log("已请求停止：当前步骤结束后中断，不写出成果。")

    def _set_running(self, running):
        state = "disabled" if running else "normal"
        self.start_button.configure(state=state)
        self.stop_button.configure(state="normal" if running else "disabled")
        for button in (self.folder_button, *self.export_buttons.values()):
            button.configure(state="disabled")
        self._finished = not running
        if running:
            self.status_var.set("处理中…")
            self.message_var.set("正在启动…")
            self.percent_var.set("0%")
            self.progress_var.set(0)
            self._percent = 0.0

    def _reset_results(self):
        for tree in (self.summary_tree, self.issue_tree):
            tree.delete(*tree.get_children())
        self.log_view.configure(state="normal")
        self.log_view.delete("1.0", "end")
        self.log_view.configure(state="disabled")
        self.payload = None
        self.run_dir = None

    def _pump(self):
        """Drain worker messages on the Tk thread; coalesce progress updates."""
        if self._closing:
            return
        latest = None
        finished = False
        try:
            for _ in range(300):
                kind, value = self.queue.get_nowait()
                if kind == "progress":
                    latest = value
                elif kind == "done":
                    self._on_done(value)
                    finished = True
                elif kind == "error":
                    self._on_error(value)
                    finished = True
                elif kind == "cancelled":
                    self._on_cancelled()
                    finished = True
                elif kind == "export_done":
                    self._on_export_done(value)
                elif kind == "export_error":
                    self._on_export_error(value)
        except queue.Empty:
            pass
        if latest is not None and not (finished or self._finished):
            self.message_var.set(latest)
            percent = max(self._percent, max(0.0, min(100.0, float(progress_percent(latest)))))
            self._percent = percent
            self.progress_var.set(percent)
            self.percent_var.set(f"{percent:.0f}%")
        self.root.after(120, self._pump)

    def _on_done(self, payload):
        elapsed = time.perf_counter() - self.started_at
        self.payload = payload
        self.run_dir = Path(payload["output_dir"])
        report = payload["report"]
        self.progress_var.set(100.0)
        self._percent = 100.0
        self.percent_var.set("100%")
        self.message_var.set("处理完成")
        self._set_running(False)
        self._fill_summary(payload)
        self._fill_issues(report)
        self._log(f"完成：{report.feature_count} 个图斑，{report.issue_count} 个问题，用时 {elapsed:.1f} 秒")
        preset = payload.get("preset")
        if preset:
            self._log(f"投影：{preset['source_crs']} → {preset['analysis_crs']}；面积阈值 0.1 亩 = "
                      f"{preset['min_area_m2']:.2f} m²")
        if payload.get("repair_summary") is not None:
            summary = payload["repair_summary"]
            if summary:
                detail = "，".join(f"{key} {value}" for key, value in sorted(summary.items()))
                self._log(f"修复记录：{detail}")
            else:
                self._log("修复记录：没有需要处理的图斑")
        if payload.get("repair_error"):
            self._log(f"自动修复失败（检查结果仍有效）：{payload['repair_error']}")
        verification = payload.get("repair_verification")
        if verification:
            state = "通过" if verification.get("passed") else "存在未解决图斑"
            self._log(f"处理后复核：{state}；多部件残留 {verification.get('remaining_multipart', 0)}，"
                      f"小于 0.1 亩残留 {verification.get('remaining_small_area', 0)}，"
                      f"重叠残留 {verification.get('overlap_pairs', 0)} 对；"
                      f"小面积循环 {verification.get('small_area_rounds', 0)} 轮，"
                      f"重叠循环 {verification.get('overlap_rounds', 0)} 轮")
        if payload.get("vector_export_error"):
            self._log(f"带问题字段的矢量副本导出失败：{payload['vector_export_error']}")
        self._log(f"成果目录：{self.run_dir}")
        self.folder_button.configure(state="normal")
        self.export_buttons["checked"].configure(state="normal")
        if (self.run_dir / "repair_staging.gpkg").is_file():
            self.export_buttons["repaired"].configure(state="normal")
        self.status_var.set(f"完成 · 用时 {elapsed:.1f} 秒")

    def _fill_summary(self, payload):
        report = payload["report"]
        rows = [("图斑数", report.feature_count, f"图层 {report.layer or '自动'}，CRS {report.crs}"),
                ("问题数", report.issue_count, "报告内含全部问题，明细表最多显示 "
                                               f"{ISSUE_ROWS} 条")]
        for kind, count in sorted(report.summary.items(), key=lambda item: (-item[1], item[0])):
            rows.append((kind, count, ""))
        if report.sampled:
            rows.append(("候选对抽样", report.checked_pair_count,
                         f"共 {report.candidate_count} 对候选，结果不完整"))
        summary = payload.get("repair_summary")
        if summary is not None:
            for status, count in sorted(summary.items()):
                rows.append((f"修复·{status}", count, ""))
        verification = payload.get("repair_verification")
        if verification:
            rows.append(("复核通过", "是" if verification.get("passed") else "否",
                         f"多部件 {verification.get('remaining_multipart', 0)}，"
                         f"小面积 {verification.get('remaining_small_area', 0)}，"
                         f"重叠 {verification.get('overlap_pairs', 0)} 对"))
        for row in rows:
            self.summary_tree.insert("", "end", values=row)

    def _fill_issues(self, report):
        issues = report.issues
        for index, issue in enumerate(issues[:ISSUE_ROWS]):
            self.issue_tree.insert("", "end", tags=(issue.severity,), values=(
                issue.issue_type, issue.severity, issue.feature_id,
                issue.related_feature_id or "-",
                " ".join(str(issue.message).split())[:160]))
        if len(issues) > ISSUE_ROWS:
            self._log(f"问题明细只显示前 {ISSUE_ROWS} 条（共 {len(issues)} 条），完整清单见 report.csv。")
        self.notebook.select(0 if issues else min(1, len(self.notebook.tabs()) - 1))

    def _on_cancelled(self):
        self._set_running(False)
        self.message_var.set("已停止")
        self.percent_var.set("")
        self.progress_var.set(0.0)
        self._percent = 0.0
        self.status_var.set("已停止，未写出成果")
        self._log("任务已停止：当前步骤结束后中止，成果目录未写入新结果。")

    def _on_error(self, text):
        from tkinter import messagebox

        self._set_running(False)
        self.message_var.set("处理失败")
        self.status_var.set("处理失败")
        self._log("处理失败：\n" + text.strip())
        last_line = [line for line in text.strip().splitlines() if line.strip()][-1]
        messagebox.showerror(APP_NAME, f"处理失败：\n{last_line}\n\n详细日志：{LOG_FILE}")

    def export_shp(self, kind):
        from tkinter import filedialog, messagebox

        if self.run_dir is None:
            return
        source = self.run_dir / ("repair_staging.gpkg" if kind == "repaired" else "checked_features.gpkg")
        layer = "processed" if kind == "repaired" else "checked_features"
        if not source.is_file():
            messagebox.showwarning(APP_NAME, f"找不到 {source.name}，请先完成一次处理。")
            return
        default = "修复成果_shp.zip" if kind == "repaired" else "检查成果_shp.zip"
        target = filedialog.asksaveasfilename(title="导出 SHP 压缩包", defaultextension=".zip",
                                              initialfile=default, filetypes=[("ZIP 压缩包", "*.zip")])
        if not target:
            return
        self.message_var.set("正在导出 SHP…")
        self.status_var.set("正在导出 SHP…")

        def work():
            try:
                path = write_shp_zip(source, layer, Path(target))
            except Exception:
                self.queue.put(("export_error", traceback.format_exc()))
            else:
                self.queue.put(("export_done", path))

        threading.Thread(target=work, name="vectopo-export", daemon=True).start()

    def _on_export_done(self, path):
        self.message_var.set("SHP 已导出")
        self.status_var.set(f"SHP 已导出：{path}")
        self._log(f"SHP 已导出：{path}")

    def _on_export_error(self, text):
        self.message_var.set("SHP 导出失败")
        self.status_var.set("SHP 导出失败")
        self._log("SHP 导出失败：\n" + text.strip())

    def open_folder(self):
        if self.run_dir is None:
            return
        try:
            os.startfile(self.run_dir)  # noqa: S606 - Windows only, opens Explorer
        except Exception as exc:
            self._log(f"无法打开目录 {self.run_dir}：{exc}")

    def _log(self, message):
        self.log_view.configure(state="normal")
        self.log_view.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n")
        self.log_view.see("end")
        self.log_view.configure(state="disabled")
        log(message)

    def close(self):
        self._closing = True
        self.cancel.set()
        for folder in self.temp_dirs:
            try:
                import shutil

                shutil.rmtree(folder, ignore_errors=True)
            except Exception:
                pass
        self.temp_dirs.clear()
        self.root.destroy()


def launch(initial_path=None, output_dir=None):
    """Open the desktop window (the default entry point of the packaged EXE)."""
    import tkinter as tk

    enable_dpi_awareness()
    root = tk.Tk()
    DesktopApp(root, initial_path=initial_path, output_dir=output_dir)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(launch())
