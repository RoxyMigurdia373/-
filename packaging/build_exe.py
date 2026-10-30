"""Build the shareable single-file Windows executable, optionally self-testing it.

Called through ``packaging/build_exe.ps1`` or directly:

    .\\.venv\\Scripts\\python.exe packaging\\build_exe.py --selftest
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPEC = ROOT / "packaging" / "vectopo_check.spec"
LOG = BUILD / "pyinstaller-build.log"


def build(clean=True):
    from vectopo_check import __version__
    from vectopo_check.launcher import APP_NAME

    command = [sys.executable, "-m", "PyInstaller", "--noconfirm",
               "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller")]
    if clean:
        command.append("--clean")
    command.append(str(SPEC))

    BUILD.mkdir(exist_ok=True)
    print(f"打包版本：{__version__}")
    print("执行：" + " ".join(command))
    result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    LOG.write_text(result.stdout or "", encoding="utf-8")
    if result.returncode != 0:
        print("\n".join((result.stdout or "").splitlines()[-25:]))
        raise SystemExit(f"PyInstaller 打包失败（退出码 {result.returncode}），完整日志：{LOG}")

    raw = DIST / "VectopoCheck.exe"
    if not raw.is_file():
        raise SystemExit(f"打包产物缺失：{raw}")
    target = DIST / f"{APP_NAME}_v{__version__}.exe"
    try:
        shutil.copyfile(raw, target)
    except PermissionError:
        raise SystemExit(f"{target.name} 正在运行，请先关闭它再重新打包。")
    size_mb = target.stat().st_size / (1024 * 1024)
    print(f"已生成：{target}（{size_mb:.1f} MB）")
    return target


def selftest(exe):
    report = DIST / "selftest.json"
    report.unlink(missing_ok=True)
    print("运行打包自检…")
    subprocess.run([str(exe), "--selftest", str(report)], cwd=DIST, check=False)
    if not report.is_file():
        raise SystemExit("自检没有生成报告，请查看 %LOCALAPPDATA%\\vectopo-check\\launcher.log")

    data = json.loads(report.read_text(encoding="utf-8"))
    for item in data["checks"]:
        mark = "OK  " if item["ok"] else "FAIL"
        print(f"  [{mark}] {item['name']}: {item['detail']}")
    if not data["ok"]:
        raise SystemExit("自检失败，请检查上面的 FAIL 项")
    print(f"自检通过（{len(data['checks'])} 项）")


def main(argv=None):
    parser = argparse.ArgumentParser(description="打包并验证 Windows 单文件可执行程序")
    parser.add_argument("--selftest", action="store_true", help="打包后运行 EXE 自检")
    parser.add_argument("--no-clean", action="store_true", help="复用上次构建缓存（改动资源后不建议）")
    args = parser.parse_args(argv)

    exe = build(clean=not args.no_clean)
    if args.selftest:
        selftest(exe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
