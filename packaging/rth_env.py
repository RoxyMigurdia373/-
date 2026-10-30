"""PyInstaller runtime hook: point PROJ/GDAL at the data bundled in the EXE.

pyproj and pyogrio find their data directories relative to their package, which
still works inside ``sys._MEIPASS``; the environment variables are set as a
fallback for code paths that look them up directly. Runs before anything else
is imported.
"""

import os
import sys

_BASE = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)

_CANDIDATES = (
    ("PROJ_DATA", os.path.join("pyproj", "proj_dir", "share", "proj")),
    ("PROJ_LIB", os.path.join("pyproj", "proj_dir", "share", "proj")),
    ("GDAL_DATA", os.path.join("pyogrio", "gdal_data")),
)

for _name, _relative in _CANDIDATES:
    _path = os.path.join(_BASE, _relative)
    if os.path.isdir(_path) and not os.environ.get(_name):
        os.environ[_name] = _path
