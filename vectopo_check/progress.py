"""Stage-weighted work progress, not an estimate of elapsed time."""
import re


def progress_percent(message):
    match = re.search(r"(\d+) / (\d+)", message)
    ratio = min(1, int(match[1]) / max(1, int(match[2]))) if match else 0
    if "processed / staging" in message:
        return 97
    for phase, start in [("INVALID_HOLE", 65), ("TOUCH_ISLAND", 75), ("SMALL_AREA", 85)]:
        if "正在处理 " + phase in message:
            return int(start + 10 * ratio)
    if "正在切开" in message or "修复候选" in message:
        return 65
    if "导出带问题" in message:
        return 62
    if "写入报告" in message:
        return 58
    if "正在检查图斑" in message:
        return int(10 + 25 * ratio)
    if "正在检查 " in message:
        return int(40 + 15 * ratio)
    if "空间索引" in message:
        return 36
    if "米制坐标" in message:
        return 5
    return 2
