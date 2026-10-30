"""One in-process check run, shared by the desktop window and the local web UI.

Both front ends need exactly the same sequence: read the layer, apply the parcel
preset, run the checks, write the reports, then optionally repair and re-verify.
Keeping it here means the native window never goes through HTTP, base64 uploads
or a temporary copy of the dataset.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .config import Config
from .engine import check_layer, run_repair
from .export import write_report_bundle, write_vector_export
from .io import read_layer
from .preset import prepare_parcels
from .report import write_reports

SIMPLE_HINT = "正在自动选择米制坐标系，面积阈值固定为 0.1 亩…"
REPAIR_SIMPLE_HINT = "正在切开岛洞并合并小于0.1亩图斑，逐项复核邻接关系…"
REPAIR_HINT = "正在生成修复候选并逐事务全图层复检，耗时可能较长…"


class Cancelled(Exception):
    """Raised by a progress callback when the user stops a run."""


def guarded_progress(progress=None, cancel=None):
    """Wrap a progress callback so a cancel request interrupts the run cheaply."""
    def report(message):
        if cancel is not None and cancel():
            raise Cancelled(message)
        if progress is not None:
            progress(message)
    return report


def run_pipeline(path, layer=None, cfg=None, output_dir=None, repair=False,
                 progress=None, cancel=None, exports=True):
    """Check one layer, optionally repair it, and write every report to disk.

    Returns a result dict (report, repair summary/verification, export errors,
    the declared config and the loaded frame). Raises :class:`Cancelled` before
    writing anything when the caller asked to stop.
    """
    cfg = cfg or Config()
    out = Path(output_dir) if output_dir is not None else Path(cfg.output_dir)
    report_progress = guarded_progress(progress, cancel)
    report_progress("正在读取矢量数据…")
    frame = read_layer(path, layer)
    preset = None
    if cfg.simple_mode:
        report_progress(SIMPLE_HINT)
        frame, cfg, preset = prepare_parcels(frame, cfg)
    report = check_layer(frame, cfg, input_path=Path(path).name, layer=layer, progress=report_progress)
    report_progress("检查完成，正在写入报告…")
    out.mkdir(parents=True, exist_ok=True)
    write_reports(report, out)
    write_report_bundle(out)
    fields, export_error = None, None
    if exports:
        try:
            report_progress("正在导出带问题字段的矢量副本…")
            fields = write_vector_export(frame, report, cfg, out)
        except Exception as exc:
            export_error = str(exc)
    repair_summary = repair_error = verification = None
    if repair:
        try:
            report_progress(REPAIR_SIMPLE_HINT if cfg.simple_mode else REPAIR_HINT)
            payload = run_repair(frame, cfg, out, path, progress=report_progress)
            repair_summary = payload["summary"]
            verification = payload.get("verification")
        except Cancelled:
            raise
        except Exception as exc:
            repair_error = str(exc)
    return {"report": report, "preset": preset, "cfg": cfg, "frame": frame,
            "severity_counts": dict(Counter(issue.severity for issue in report.issues)),
            "vector_fields": fields, "vector_export_error": export_error,
            "repair_enabled": bool(repair), "repair_summary": repair_summary,
            "repair_error": repair_error, "repair_verification": verification,
            "output_dir": out}
