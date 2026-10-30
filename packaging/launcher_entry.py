"""Entry point for the packaged executable (PyInstaller build)."""

from vectopo_check.launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
