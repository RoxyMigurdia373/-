# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: one-file, windowless Windows executable.

Build with ``pwsh -File packaging/build_exe.ps1`` (or
``.venv\\Scripts\\pyinstaller --noconfirm packaging/vectopo_check.spec``).
"""

import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))
PACKAGE = os.path.join(ROOT, "vectopo_check")

datas = [(os.path.join(PACKAGE, "static"), os.path.join("vectopo_check", "static"))]
binaries = []
hiddenimports = ["vectopo_check.cli", "vectopo_check.web", "vectopo_check.launcher",
                 "vectopo_check.desktop", "vectopo_check.pipeline",
                 "vectopo_check.repair.hole_split"]

# GIS wheels ship compiled libraries and CRS databases that the generic hooks
# do not always pick up, so collect these packages whole.
for _package in ("geopandas", "shapely", "pyproj", "pyogrio"):
    try:
        _datas, _binaries, _hidden = collect_all(_package)
    except Exception as exc:  # pragma: no cover - build environment only
        print(f"skip collect_all({_package}): {exc}")
        continue
    datas += _datas
    binaries += _binaries
    hiddenimports += _hidden

icon_path = os.path.join(ROOT, "packaging", "app.ico")
version_path = os.path.join(ROOT, "packaging", "version_info.txt")

a = Analysis(
    [os.path.join(ROOT, "packaging", "launcher_entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(ROOT, "packaging", "rth_env.py")],
    excludes=["matplotlib", "streamlit", "PIL", "IPython", "pytest", "_pytest", "fiona", "scipy", "notebook"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VectopoCheck",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path if os.path.exists(icon_path) else None,
    version=version_path if os.path.exists(version_path) else None,
)
